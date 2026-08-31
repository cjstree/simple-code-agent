import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_agent_evals import runner
from code_agent_evals import solver as eval_solver
from code_agent_evals.runner import apply_agent_config, parse_payload
from code_agent_evals.solver import _runner_payload
from code_agent_evals.tasks import basic_agent_eval


def test_basic_agent_eval_uses_isolated_file_fixture() -> None:
    task = basic_agent_eval()

    assert task.sandbox is not None
    assert task.sandbox.type == "local"
    assert len(task.dataset) == 3

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
    # Solver metadata is forwarded unchanged to the subprocess runner.
    state = SimpleNamespace(
        input_text="fallback",
        metadata={
            "turns": ["first", "second"],
            "agent_config": {"compact_thresh_hold": 600},
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
