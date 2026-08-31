import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_agent_evals import runner
from code_agent_evals import scorer as eval_scorer
from code_agent_evals import solver as eval_solver
from code_agent_evals.runner import apply_agent_config, parse_payload
from code_agent_evals.scorer import evaluation_contract, score_tests
from code_agent_evals.solver import _runner_payload
from code_agent_evals.tasks import basic_agent_eval


def test_basic_agent_eval_uses_isolated_file_fixture() -> None:
    task = basic_agent_eval()

    assert task.sandbox is not None
    assert task.sandbox.type == "local"
    assert len(task.dataset) == 7

    samples = {sample.id: sample for sample in task.dataset}
    sample = samples["fix_add"]
    assert sample.files is not None
    fixture = Path(sample.files["."])
    assert fixture.name == "fix_add"
    assert (fixture / "calculator.py").is_file()
    assert (fixture / "tests/test_calculator.py").is_file()


def test_context_fixture_includes_skills_and_memory() -> None:
    task = basic_agent_eval()
    samples = {sample.id: sample for sample in task.dataset}

    sample = samples["fix_multiply_with_context"]
    assert sample.files is not None
    fixture = Path(sample.files["."])

    assert (fixture / "calculator.py").is_file()
    assert (fixture / "tests/test_calculator.py").is_file()
    assert (fixture / "skills/arithmetic-fix/SKILL.md").is_file()
    assert (fixture / "memory/MEMORY.md").is_file()
    assert (fixture / "memory/project-arithmetic.md").is_file()


# A multi-turn sample carries ordered prompts and compact thresholds in metadata.
# Parsing preserves both so the runner can reuse one Agent for every turn.
def test_multiturn_eval_payload_is_available_to_runner() -> None:
    # The compact sample preserves its turns and Session-specific threshold.
    task = basic_agent_eval()
    samples = {sample.id: sample for sample in task.dataset}
    sample = samples["fix_add_multiturn_compact"]

    turns, config = parse_payload(
        json.dumps(
            {
                "turns": sample.metadata["turns"],
                "agent_config": sample.metadata["agent_config"],
            }
        )
    )

    assert len(turns) == 3
    assert turns[-1] == "Now fix the implementation and run the tests."
    assert config == {"compact_thresh_hold": 500}


# Existing single-prompt callers remain valid while context settings are
# restricted to the allowlisted evaluation thresholds.
def test_runner_accepts_legacy_prompt_and_applies_session_config() -> None:
    # Legacy prompts still work and eval thresholds configure the Session.
    assert parse_payload("plain prompt") == (["plain prompt"], {})
    assert parse_payload('"JSON-shaped prompt"') == (['"JSON-shaped prompt"'], {})
    session = SimpleNamespace(compact_thresh_hold=128_000)
    agent = SimpleNamespace(session=session)

    apply_agent_config(agent, {"compact_thresh_hold": 2_000})

    assert session.compact_thresh_hold == 2_000


def test_runner_rejects_obsolete_context_manager_config() -> None:
    # Eval config must not silently target the retired context manager.
    payload = json.dumps({"turns": ["first"], "agent_config": {"context_limit": 500}})

    with pytest.raises(ValueError, match="unsupported agent_config fields"):
        parse_payload(payload)


# The solver uses the sample input for legacy records and forwards explicit
# turns and context thresholds when metadata supplies them.
def test_solver_serializes_multiturn_runner_payload() -> None:
    # Runner settings are forwarded while scorer-only test contracts stay hidden.
    state = SimpleNamespace(
        input_text="fallback",
        metadata={
            "turns": ["first", "second"],
            "agent_config": {"compact_thresh_hold": 600},
            "FAIL_TO_PASS": ["tests/test_hidden_contract.py::test_fix"],
            "PASS_TO_PASS": ["tests/test_hidden_contract.py::test_existing"],
        },
    )

    assert json.loads(_runner_payload(state)) == {
        "turns": ["first", "second"],
        "agent_config": {"compact_thresh_hold": 600},
    }


# The eval solver starts the runner directly without a model bridge or injected
# placeholder credentials, leaving the Agent's own LLM settings authoritative.
@pytest.mark.asyncio
async def test_solver_runs_agent_without_bridge_or_llm_overrides(monkeypatch) -> None:
    calls = []

    class FakeSandbox:
        async def exec(self, command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(success=True, stdout="final answer", stderr="")

    monkeypatch.setattr(eval_solver, "sandbox", lambda: FakeSandbox())
    state = SimpleNamespace(input_text="fix it", metadata=None, output=None)

    result = await eval_solver.my_agent_solver()(state, None)

    assert result is state
    assert calls == [
        (
            [sys.executable, "-m", "code_agent_evals.runner"],
            {
                "input": json.dumps(
                    {"turns": ["fix it"], "agent_config": {}},
                    ensure_ascii=False,
                ),
                "timeout": 300,
            },
        )
    ]
    assert state.output.completion == "final answer"


# The first turn creates the eval session and every later turn reuses it; the
# final response is returned only after the shared Agent is closed.
@pytest.mark.asyncio
async def test_runner_executes_turns_in_one_agent_session(monkeypatch) -> None:
    # All turns share one Agent Session configured before the first turn.
    agents = []

    class FakeAgent:
        def __init__(self, telemetry) -> None:
            self.telemetry = telemetry
            self.session = SimpleNamespace(compact_thresh_hold=128_000)
            self.calls: list[tuple[str, bool]] = []
            self.closed = False
            agents.append(self)

        async def start(self) -> None:
            return None

        async def run(self, turn: str, *, new_session: bool = True) -> str:
            self.calls.append((turn, new_session))
            return f"answer: {turn}"

        async def close(self) -> None:
            self.closed = True

    telemetry = SimpleNamespace(shutdown=lambda: None)
    monkeypatch.setattr(runner, "Agent", FakeAgent)
    monkeypatch.setattr(
        runner,
        "AgentTelemetry",
        SimpleNamespace(initialize=lambda **kwargs: telemetry),
    )

    result = await runner.run(
        ["first", "second", "third"], {"compact_thresh_hold": 500}
    )

    assert result == "answer: third"
    assert agents[0].calls == [
        ("first", True),
        ("second", False),
        ("third", False),
    ]
    assert agents[0].session.compact_thresh_hold == 500
    assert agents[0].closed is True


def test_evaluation_contract_requires_safe_disjoint_test_groups() -> None:
    # Mini-SWE metadata cannot omit a group, repeat a test, or escape test roots.
    with pytest.raises(ValueError, match="missing PASS_TO_PASS"):
        evaluation_contract({"FAIL_TO_PASS": ["tests/test_example.py::test_fix"]})

    with pytest.raises(ValueError, match="must be disjoint"):
        evaluation_contract(
            {
                "FAIL_TO_PASS": ["tests/test_example.py::test_fix"],
                "PASS_TO_PASS": ["tests/test_example.py::test_fix"],
            }
        )

    with pytest.raises(ValueError, match="unsafe test selector"):
        evaluation_contract(
            {
                "FAIL_TO_PASS": ["../test_example.py::test_fix"],
                "PASS_TO_PASS": ["tests/test_example.py::test_existing"],
            }
        )


@pytest.mark.asyncio
async def test_scorer_keeps_legacy_full_suite_behavior() -> None:
    # Samples without a mini-SWE contract still run the complete pytest suite once.
    class FakeSandbox:
        def __init__(self) -> None:
            self.commands = []

        async def exec(self, command, **kwargs):
            self.commands.append((command, kwargs))
            return SimpleNamespace(
                success=True, returncode=0, stdout="3 passed", stderr=""
            )

    environment = FakeSandbox()
    state = SimpleNamespace(sample_id="legacy", metadata=None)

    score = await score_tests(state, environment)

    assert score.value == 1
    assert environment.commands == [
        ([sys.executable, "-m", "pytest", "-q"], {"timeout": 60})
    ]


@pytest.mark.asyncio
async def test_mini_swe_scorer_injects_hidden_tests_after_agent_run(
    monkeypatch, tmp_path
) -> None:
    # Hidden files are copied only by the scorer, then both test groups are run.
    hidden_root = tmp_path / "hidden_tests"
    case_root = hidden_root / "cache_case"
    case_root.mkdir(parents=True)
    (case_root / "test_regression.py").write_text("def test_hidden(): pass\n")
    monkeypatch.setattr(eval_scorer, "_HIDDEN_TESTS_ROOT", hidden_root)

    class FakeSandbox:
        def __init__(self) -> None:
            self.writes = []
            self.commands = []

        async def write_file(self, path, contents) -> None:
            self.writes.append((path, contents))

        async def exec(self, command, **kwargs):
            self.commands.append((command, kwargs))
            return SimpleNamespace(
                success=True, returncode=0, stdout="1 passed", stderr=""
            )

    environment = FakeSandbox()
    state = SimpleNamespace(
        sample_id="cache_case",
        metadata={
            "FAIL_TO_PASS": ["tests/test_cache.py::test_expired"],
            "PASS_TO_PASS": [".eval_hidden_tests/test_regression.py::test_hidden"],
        },
    )

    score = await score_tests(state, environment)

    assert score.value == 1
    assert environment.writes == [
        (
            ".eval_hidden_tests/test_regression.py",
            b"def test_hidden(): pass\n",
        )
    ]
    assert [call[0][-1] for call in environment.commands] == [
        "tests/test_cache.py::test_expired",
        ".eval_hidden_tests/test_regression.py::test_hidden",
    ]
    assert score.metadata == {
        "FAIL_TO_PASS": "passed",
        "PASS_TO_PASS": "passed",
        "hidden_tests": [".eval_hidden_tests/test_regression.py"],
    }


@pytest.mark.asyncio
async def test_mini_swe_scorer_requires_both_groups_to_pass(
    monkeypatch, tmp_path
) -> None:
    # A compatibility regression produces score zero even when the fix tests pass.
    hidden_root = tmp_path / "hidden_tests"
    case_root = hidden_root / "regression_case"
    case_root.mkdir(parents=True)
    (case_root / "test_hidden.py").write_text("def test_hidden(): pass\n")
    monkeypatch.setattr(eval_scorer, "_HIDDEN_TESTS_ROOT", hidden_root)

    class FakeSandbox:
        def __init__(self) -> None:
            self.results = iter(
                [
                    SimpleNamespace(
                        success=True, returncode=0, stdout="passed", stderr=""
                    ),
                    SimpleNamespace(
                        success=False, returncode=1, stdout="failed", stderr=""
                    ),
                ]
            )

        async def write_file(self, path, contents) -> None:
            return None

        async def exec(self, command, **kwargs):
            return next(self.results)

    state = SimpleNamespace(
        sample_id="regression_case",
        metadata={
            "FAIL_TO_PASS": ["tests/test_fix.py::test_fix"],
            "PASS_TO_PASS": [".eval_hidden_tests/test_hidden.py::test_hidden"],
        },
    )

    score = await score_tests(state, FakeSandbox())

    assert score.value == 0
    assert score.metadata["FAIL_TO_PASS"] == "passed"
    assert score.metadata["PASS_TO_PASS"] == "failed"


def test_mini_swe_samples_define_levels_and_external_hidden_tests() -> None:
    # The dataset contains two L2 and two L3 cases with scorer-only test sources.
    task = basic_agent_eval()
    samples = {
        str(sample.id): sample
        for sample in task.dataset
        if (sample.metadata or {}).get("level") in {"L2", "L3"}
    }

    assert len(samples) == 4
    assert [sample.metadata["level"] for sample in samples.values()].count("L2") == 2
    assert [sample.metadata["level"] for sample in samples.values()].count("L3") == 2

    project_root = Path(__file__).resolve().parents[1]
    for sample_id, sample in samples.items():
        contract = evaluation_contract(sample.metadata)
        assert contract is not None
        assert sample.metadata["regression_purpose"]
        reference_patch = project_root / "evals" / sample.metadata["reference_patch"]
        assert reference_patch.is_file()
        touched_files = reference_patch.read_text().count("diff --git ")
        if sample.metadata["level"] == "L2":
            assert touched_files == 1
        else:
            assert touched_files >= 2
        assert sample.files is not None
        fixture = Path(sample.files["."])
        assert not (fixture / ".eval_hidden_tests").exists()
        assert (project_root / "evals" / "hidden_tests" / sample_id).is_dir()


def test_pristine_mini_swe_fixtures_fail_only_regressions() -> None:
    # Buggy fixtures fail every FAIL_TO_PASS group while preserving PASS_TO_PASS.
    project_root = Path(__file__).resolve().parents[1]
    task = basic_agent_eval()
    samples = [
        sample
        for sample in task.dataset
        if (sample.metadata or {}).get("level") in {"L2", "L3"}
    ]

    def source_selector(sample_id: str, fixture: Path, selector: str) -> str:
        path, *nodes = selector.split("::")
        if path.startswith(".eval_hidden_tests/"):
            relative = path.removeprefix(".eval_hidden_tests/")
            source = project_root / "evals" / "hidden_tests" / sample_id / relative
        else:
            source = fixture / path
        return "::".join([str(source), *nodes])

    for sample in samples:
        sample_id = str(sample.id)
        fixture = Path(sample.files["."])
        contract = evaluation_contract(sample.metadata)
        assert contract is not None
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(fixture),
        }

        fail_to_pass = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *(
                    source_selector(sample_id, fixture, selector)
                    for selector in contract.fail_to_pass
                ),
            ],
            cwd=fixture,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        pass_to_pass = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *(
                    source_selector(sample_id, fixture, selector)
                    for selector in contract.pass_to_pass
                ),
            ],
            cwd=fixture,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert fail_to_pass.returncode == 1, fail_to_pass.stdout + fail_to_pass.stderr
        assert pass_to_pass.returncode == 0, pass_to_pass.stdout + pass_to_pass.stderr


def test_reference_patches_satisfy_visible_and_hidden_contracts(tmp_path) -> None:
    # Each reference fix makes both test groups pass in an isolated fixture copy.
    project_root = Path(__file__).resolve().parents[1]
    task = basic_agent_eval()
    samples = [
        sample
        for sample in task.dataset
        if (sample.metadata or {}).get("level") in {"L2", "L3"}
    ]

    for sample in samples:
        sample_id = str(sample.id)
        workspace = tmp_path / sample_id
        shutil.copytree(Path(sample.files["."]), workspace)
        shutil.copytree(
            project_root / "evals" / "hidden_tests" / sample_id,
            workspace / ".eval_hidden_tests",
        )
        reference_patch = project_root / "evals" / sample.metadata["reference_patch"]
        applied = subprocess.run(
            ["git", "apply", str(reference_patch)],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        assert applied.returncode == 0, applied.stdout + applied.stderr

        contract = evaluation_contract(sample.metadata)
        assert contract is not None
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *contract.fail_to_pass,
                *contract.pass_to_pass,
            ],
            cwd=workspace,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(workspace),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
