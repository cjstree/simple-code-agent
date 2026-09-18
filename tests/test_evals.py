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
from code_agent_evals.scorer import (
    evaluation_contract,
    preservation_checks,
    preservation_trace_result,
    score_tests,
)
from code_agent_evals.solver import _runner_payload
from code_agent_evals.tasks import basic_agent_eval, context_survival_eval
from code_agent_evals.trace import parse_trace_jsonl


def test_basic_agent_eval_uses_isolated_file_fixture() -> None:
    task = basic_agent_eval()

    assert task.sandbox is not None
    assert task.sandbox.type == "local"
    assert len(task.dataset) == 9

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

    turns, config, restart_after_turns = parse_payload(
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
    assert restart_after_turns == ()


# Existing single-prompt callers remain valid while context settings are
# restricted to the allowlisted evaluation thresholds.
def test_runner_accepts_legacy_prompt_and_applies_session_config() -> None:
    # Legacy prompts still work and eval thresholds configure the Session.
    assert parse_payload("plain prompt") == (["plain prompt"], {}, ())
    assert parse_payload('"JSON-shaped prompt"') == (
        ['"JSON-shaped prompt"'],
        {},
        (),
    )
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
        "restart_agent_after_turns": [],
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
                    {
                        "turns": ["fix it"],
                        "agent_config": {},
                        "restart_agent_after_turns": [],
                    },
                    ensure_ascii=False,
                ),
                "timeout": 300,
            },
        )
    ]
    assert state.output.completion == "final answer"


@pytest.mark.asyncio
async def test_solver_uses_sample_timeout_without_forwarding_it(monkeypatch) -> None:
    # A long sample gets more runner time while the Agent receives only its turns.
    calls = []

    class FakeSandbox:
        async def exec(self, command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(success=True, stdout="done", stderr="")

    monkeypatch.setattr(eval_solver, "sandbox", lambda: FakeSandbox())
    sample = next(iter(context_survival_eval().dataset))
    state = SimpleNamespace(
        input_text=sample.input, metadata=sample.metadata, output=None
    )

    await eval_solver.my_agent_solver()(state, None)

    assert calls[0]["timeout"] == sample.metadata["runner_timeout_seconds"]
    assert "runner_timeout_seconds" not in json.loads(calls[0]["input"])
    assert state.output.completion == "done"
    for invalid in (0, True, "720"):
        state.metadata = {"runner_timeout_seconds": invalid}
        with pytest.raises(ValueError, match="positive integer"):
            await eval_solver.my_agent_solver()(state, None)
    assert len(calls) == 1


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
    telemetry_settings = []

    def initialize_telemetry(**kwargs):
        telemetry_settings.append(kwargs)
        return telemetry

    monkeypatch.setattr(runner, "Agent", FakeAgent)
    monkeypatch.setattr(
        runner,
        "AgentTelemetry",
        SimpleNamespace(initialize=initialize_telemetry),
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
    assert telemetry_settings[0]["trace_log_dir"].as_posix() == ".eval_traces"


@pytest.mark.asyncio
async def test_runner_restarts_agent_between_memory_handoff_episodes(
    monkeypatch,
) -> None:
    # A requested restart closes the old Agent and starts a fresh configured session.
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
        ["learn convention", "recall convention", "implement"],
        {"compact_thresh_hold": 1_200},
        (1,),
    )

    assert result == "answer: implement"
    assert [agent.calls for agent in agents] == [
        [("learn convention", True)],
        [("recall convention", True), ("implement", False)],
    ]
    assert [agent.session.compact_thresh_hold for agent in agents] == [1_200, 1_200]
    assert all(agent.closed for agent in agents)


def test_runner_validates_agent_restart_boundaries() -> None:
    # Restarts may occur once after any turn except the final turn.
    turns, _, restart_after_turns = parse_payload(
        json.dumps(
            {
                "turns": ["first", "second", "third"],
                "restart_agent_after_turns": [2, 1],
            }
        )
    )

    assert turns == ["first", "second", "third"]
    assert restart_after_turns == (1, 2)

    with pytest.raises(ValueError, match="non-final turn"):
        parse_payload(
            json.dumps(
                {
                    "turns": ["first", "second"],
                    "restart_agent_after_turns": [2],
                }
            )
        )

    with pytest.raises(ValueError, match="duplicates"):
        parse_payload(
            json.dumps(
                {
                    "turns": ["first", "second"],
                    "restart_agent_after_turns": [1, 1],
                }
            )
        )


@pytest.mark.asyncio
async def test_runner_cleans_up_when_replacement_agent_cannot_start(
    monkeypatch,
) -> None:
    # A failed episode restart closes both Agent instances and shuts down telemetry.
    agents = []

    class FakeAgent:
        def __init__(self, telemetry) -> None:
            self.telemetry = telemetry
            self.session = SimpleNamespace(compact_thresh_hold=128_000)
            self.closed = False
            agents.append(self)

        async def start(self) -> None:
            if len(agents) == 2:
                raise RuntimeError("replacement startup failed")

        async def run(self, turn: str, *, new_session: bool = True) -> str:
            return turn

        async def close(self) -> None:
            self.closed = True

    telemetry = SimpleNamespace(shutdown_called=False)

    def shutdown() -> None:
        telemetry.shutdown_called = True

    telemetry.shutdown = shutdown
    monkeypatch.setattr(runner, "Agent", FakeAgent)
    monkeypatch.setattr(
        runner,
        "AgentTelemetry",
        SimpleNamespace(initialize=lambda **kwargs: telemetry),
    )

    with pytest.raises(RuntimeError, match="replacement startup failed"):
        await runner.run(["learn", "recall"], {}, (1,))

    assert len(agents) == 2
    assert all(agent.closed for agent in agents)
    assert telemetry.shutdown_called is True


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


def test_mixed_capability_samples_document_scenarios_and_runner_behavior() -> None:
    # The two mixed cases state their focus, overlap, trace signals, and turn topology.
    task = basic_agent_eval()
    samples = {str(sample.id): sample for sample in task.dataset}

    rollback = samples["rollback_reason_long_horizon"]
    assert rollback.metadata["focus"] == "multi_turn_compaction_following"
    assert len(rollback.metadata["turns"]) == 5
    assert rollback.metadata["agent_config"]["compact_thresh_hold"] == 1_400
    assert "history_compaction" in rollback.metadata["covers"]
    assert len(rollback.metadata["trace_expectations"]) == 2

    memory = samples["release_policy_memory_handoff"]
    assert memory.metadata["focus"] == "cross_turn_memory"
    assert memory.metadata["restart_agent_after_turns"] == [1]
    assert len(memory.metadata["turns"]) == 4
    assert "memory_extraction" in memory.metadata["covers"]
    assert "agent_restart" in memory.metadata["covers"]
    assert len(memory.metadata["trace_expectations"]) == 2

    payload = json.loads(
        _runner_payload(
            SimpleNamespace(input_text=memory.input, metadata=memory.metadata)
        )
    )
    assert payload["restart_agent_after_turns"] == [1]

    project_root = Path(__file__).resolve().parents[1]
    for sample in (rollback, memory):
        assert sample.metadata["scenario_description"]
        assert sample.metadata["regression_purpose"]
        assert evaluation_contract(sample.metadata) is not None
        assert (project_root / "evals" / sample.metadata["reference_patch"]).is_file()
        assert sample.files is not None
        assert Path(sample.files["."]).is_dir()
        assert (project_root / "evals" / "hidden_tests" / str(sample.id)).is_dir()


def test_pristine_mini_swe_fixtures_fail_only_regressions() -> None:
    # Buggy fixtures fail every FAIL_TO_PASS group while preserving PASS_TO_PASS.
    project_root = Path(__file__).resolve().parents[1]
    task = basic_agent_eval()
    samples = [
        sample
        for sample in task.dataset
        if evaluation_contract(sample.metadata) is not None
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
        if evaluation_contract(sample.metadata) is not None
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


def test_constraint_sample_declares_isolated_scoring_contract() -> None:
    # Three distinct constraints are scored on an isolated input fixture.
    samples = list(context_survival_eval().dataset)
    assert len(samples) == 1
    sample = samples[0]
    assert sample.id == "constraint_rollback_compatibility"
    checks = preservation_checks(sample.metadata)
    assert checks is not None and [check.id for check in checks] == [
        "C-11",
        "C-12",
        "C-13",
    ]
    assert all(check.category == "constraint" for check in checks)
    assert all(check.min_compact_hops == 2 for check in checks)
    assert [check.introduced_turn for check in checks] == [1, 1, 2]
    payload = json.loads(
        _runner_payload(
            SimpleNamespace(input_text=sample.input, metadata=sample.metadata)
        )
    )
    assert "context_survival" not in payload
    assert payload["agent_config"] == {
        "compact_thresh_hold": 4_000,
        "reserved_token": 700,
        "max_tool_res": 3,
    }
    turns = payload["turns"]
    assert 9 <= len(turns) <= 11
    assert all(check.used_turn == len(turns) for check in checks)
    assert [index for index, turn in enumerate(turns, 1) if "C-11" in turn] == [1]
    assert [index for index, turn in enumerate(turns, 1) if "C-12" in turn] == [1]
    assert [index for index, turn in enumerate(turns, 1) if "C-13" in turn] == [2]
    assert Path(sample.files["."]).name == "constraint_rollback_compatibility"


def test_constraint_fixture_regressions_and_reference_patch(tmp_path) -> None:
    # Every declared constraint catches a baseline failure; the reference fixes all.
    sample = next(iter(context_survival_eval().dataset))
    workspace = tmp_path / str(sample.id)
    shutil.copytree(Path(sample.files["."]), workspace)
    project_root = Path(__file__).resolve().parents[1]
    shutil.copytree(
        project_root / "evals" / "hidden_tests" / str(sample.id),
        workspace / ".eval_hidden_tests",
    )
    checks = preservation_checks(sample.metadata)
    assert checks is not None
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(workspace),
    }

    def run_tests(selectors):
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *selectors],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    for check in checks:
        baseline = run_tests(check.behavior_tests)
        assert baseline.returncode == 1, (check.id, baseline.stdout, baseline.stderr)

    patch = project_root / "evals" / sample.metadata["reference_patch"]
    applied = subprocess.run(
        ["git", "apply", str(patch)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr

    selectors = tuple(selector for check in checks for selector in check.behavior_tests)
    reference = run_tests(selectors)
    assert reference.returncode == 0, reference.stdout + reference.stderr

    service = workspace / "deployment_service.py"
    source = service.read_text()
    assert source.count("reason: str) -> Deployment:") == 1
    service.write_text(
        source.replace(
            "reason: str) -> Deployment:", "reason: str = 'unspecified') -> Deployment:"
        )
    )
    signature_regression = run_tests(
        (
            ".eval_hidden_tests/test_constraint_regression.py::test_rollback_signature_stays_compatible",
        )
    )
    assert signature_regression.returncode == 1, (
        signature_regression.stdout + signature_regression.stderr
    )
    locally_passing = run_tests(("tests/test_rollback.py",))
    assert locally_passing.returncode == 0, (
        locally_passing.stdout + locally_passing.stderr
    )


@pytest.mark.parametrize(
    ("summaries", "expected"),
    [
        (["C-11 C-12 C-13", "C-11 C-12 C-13"], (True, True, 2)),
        (["C-11 C-12 C-13", "C-12 C-13"], (True, False, 2)),
        (["C-11 C-12 C-13"], (False, False, 1)),
    ],
)
def test_preservation_trace_requires_two_marked_compactions(
    summaries, expected
) -> None:
    # A missing marker or compact hop fails the first constraint's trace gate.
    sample = next(iter(context_survival_eval().dataset))
    check = preservation_checks(sample.metadata)[0]
    spans = []
    for ordinal in range(1, check.used_turn + 1):
        spans.append(
            {
                "schema_version": 1,
                "name": "agent.turn",
                "context": {"trace_id": "trace", "span_id": f"turn-{ordinal}"},
                "parent_id": None,
                "start_time": f"2026-09-14T00:{ordinal:02d}:00Z",
                "end_time": f"2026-09-14T00:{ordinal:02d}:05Z",
                "attributes": {"session.id": "session"},
            }
        )
    for index, summary in enumerate(summaries):
        spans.append(
            {
                "schema_version": 1,
                "name": "session.compact_history",
                "context": {"trace_id": "trace", "span_id": f"compact-{index}"},
                "parent_id": f"turn-{index + 3}",
                "start_time": f"2026-09-14T00:{index + 3:02d}:01Z",
                "end_time": f"2026-09-14T00:{index + 3:02d}:02Z",
                "attributes": {"session.id": "session", "output.value": summary},
            }
        )
    trace = parse_trace_jsonl(
        json.dumps(
            {"schema_version": 1, "session_id": "session", "file": "trace.jsonl"}
        ),
        {"trace.jsonl": "\n".join(json.dumps(span) for span in reversed(spans))},
    )
    assert preservation_trace_result(check, trace) == expected


@pytest.mark.asyncio
async def test_context_survival_score_requires_trace_and_behavior(monkeypatch) -> None:
    # Each constraint combines compact evidence with its own behavior tests.
    sample = next(iter(context_survival_eval().dataset))
    metadata = sample.metadata

    class FakeSandbox:
        def __init__(self):
            self.failed_selectors = set()
            self.commands = []

        async def write_file(self, path, contents):
            return None

        async def exec(self, command, **kwargs):
            self.commands.append(command)
            return SimpleNamespace(
                success=command[-1] not in self.failed_selectors,
                stdout="test result",
                stderr="",
            )

    spans = [SimpleNamespace(output_value="C-11 C-12 C-13 retained") for _ in range(2)]

    async def read_trace(_environment):
        return SimpleNamespace(compact_history_between=lambda start, end: spans)

    monkeypatch.setattr(eval_scorer, "read_trace_input", read_trace)
    state = SimpleNamespace(sample_id=sample.id, metadata=metadata)
    environment = FakeSandbox()

    passing = await score_tests(state, environment)
    assert passing.value == 1
    checks = passing.metadata["context_survival"]["checks"]
    assert [check["id"] for check in checks] == ["C-11", "C-12", "C-13"]
    check = checks[0]
    assert passing.metadata["context_survival"]["strict_pass"] is True
    assert check["score"] == 1
    assert check["activation"] == {
        "passed": True,
        "score": 0.25,
        "compact_hops": 2,
        "min_compact_hops": 2,
    }
    assert check["trace_preserved"] == {"passed": True, "score": 0.25}
    assert check["behavior"]["score"] == 0.5
    assert check["behavior"]["tests_total"] == 2
    assert len(environment.commands) == 7
    assert all(len(command) == 5 for command in environment.commands)

    spans.pop()
    inactive = await score_tests(state, environment)
    assert inactive.value == 0.5
    assert all(
        not check["activation"]["passed"]
        and check["trace_preserved"]["score"] == 0
        and not check["strict_pass"]
        for check in inactive.metadata["context_survival"]["checks"]
    )

    spans.append(SimpleNamespace(output_value="C-11 C-12 C-13 retained"))
    failed_selector = preservation_checks(metadata)[0].behavior_tests[0]
    environment.failed_selectors = {failed_selector}
    failed_behavior = await score_tests(state, environment)
    assert failed_behavior.value == pytest.approx(11 / 12)
    failed_check = failed_behavior.metadata["context_survival"]["checks"][0]
    assert failed_check["strict_pass"] is False
    assert failed_check["behavior"]["score"] == 0.25
    assert failed_check["behavior"]["tests_passed"] == 1
    assert failed_check["behavior"]["tests_total"] == 2
    assert failed_check["behavior"]["tests"][0] == {
        "selector": failed_selector,
        "passed": False,
    }

    environment.failed_selectors = set()
    spans[1] = SimpleNamespace(output_value="C-11 C-13 retained")
    missing_marker = await score_tests(state, environment)
    assert missing_marker.value == pytest.approx(11 / 12)
    assert [
        item["trace_preserved"]["passed"]
        for item in missing_marker.metadata["context_survival"]["checks"]
    ] == [True, False, True]
    assert missing_marker.metadata["context_survival"]["strict_pass"] is False
