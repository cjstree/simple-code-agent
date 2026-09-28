"""Non-interactive subprocess entry point used only by Inspect evaluations."""

import asyncio
import json
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

from code_agent.agent import Agent
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry
from code_agent_evals.long_horizon import capture_checkpoint, validate_checkpoint_turns

_SESSION_CONFIG_FIELDS = {
    "compact_thresh_hold",
    "max_tool_res",
    "max_tool_round_res",
    "persist_threshold",
    "reserved_token",
}


def parse_payload(
    raw: str,
) -> tuple[list[str], dict[str, int], tuple[int, ...], Path]:
    """Parse the solver-owned evaluation payload."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("evaluation runner payload must be a JSON object") from None

    if not isinstance(payload, dict):
        raise TypeError("evaluation runner payload must be an object")

    turns = payload.get("turns")
    if (
        not isinstance(turns, list)
        or not turns
        or any(not isinstance(turn, str) or not turn.strip() for turn in turns)
    ):
        raise ValueError("evaluation payload turns must be non-empty strings")

    raw_config = payload.get("agent_config", {})
    if not isinstance(raw_config, dict):
        raise TypeError("evaluation payload agent_config must be an object")

    unknown_fields = set(raw_config) - _SESSION_CONFIG_FIELDS
    if unknown_fields:
        fields = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unsupported agent_config fields: {fields}")

    agent_config: dict[str, int] = {}
    for name, value in raw_config.items():
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"agent_config {name} must be a positive integer")
        agent_config[name] = value

    raw_restarts = payload.get("restart_agent_after_turns", [])
    if not isinstance(raw_restarts, list) or any(
        not isinstance(turn_number, int) or isinstance(turn_number, bool)
        for turn_number in raw_restarts
    ):
        raise TypeError("restart_agent_after_turns must be a list of integers")
    if any(
        turn_number < 1 or turn_number >= len(turns) for turn_number in raw_restarts
    ):
        raise ValueError(
            "restart_agent_after_turns entries must identify a non-final turn"
        )
    if len(raw_restarts) != len(set(raw_restarts)):
        raise ValueError("restart_agent_after_turns cannot contain duplicates")

    raw_runtime_root = payload.get("eval_runtime_root")
    if not isinstance(raw_runtime_root, str) or not raw_runtime_root:
        raise ValueError(
            "evaluation payload eval_runtime_root must be an absolute path"
        )
    runtime_root = Path(raw_runtime_root)
    if not runtime_root.is_absolute():
        raise ValueError(
            "evaluation payload eval_runtime_root must be an absolute path"
        )

    return turns, agent_config, tuple(sorted(raw_restarts)), runtime_root


def prepare_runtime_directories(runtime_root: Path) -> tuple[Path, Path]:
    """Prepare isolated trace and memory roots before an Agent can start."""
    if not runtime_root.is_absolute() or not runtime_root.is_dir():
        raise RuntimeError("eval runtime root must be an existing absolute directory")
    traces_path = runtime_root / "traces"
    memory_path = runtime_root / "memory"
    if traces_path.exists() or memory_path.exists():
        raise RuntimeError("eval runtime root contains unexpected state")

    workspace_memory = Path("memory")
    if workspace_memory.exists() and not workspace_memory.is_dir():
        raise RuntimeError("workspace memory exists but is not a directory")

    traces_path.mkdir()
    if workspace_memory.is_dir():
        shutil.move(str(workspace_memory), str(memory_path))
    else:
        memory_path.mkdir()
    return traces_path, memory_path


def apply_agent_config(agent: Agent, config: dict[str, int]) -> None:
    """Apply the allowlisted evaluation thresholds to the active session."""
    for name, value in config.items():
        setattr(agent.session, name, value)


async def run(
    turns: list[str],
    agent_config: dict[str, int],
    restart_agent_after_turns: tuple[int, ...] = (),
    *,
    runtime_root: Path,
    checkpoint_turns: tuple[int, ...] = (),
) -> str:
    """Run evaluation turns, optionally restarting Agent between episodes."""
    if checkpoint_turns:
        validate_checkpoint_turns(list(checkpoint_turns), len(turns))
    checkpoints = {}
    traces_path, memory_path = prepare_runtime_directories(runtime_root)
    telemetry = AgentTelemetry.initialize(
        enabled=settings.phoenix_enabled,
        endpoint=settings.phoenix_collector_endpoint,
        project_name=settings.phoenix_project_name,
        trace_log_dir=traces_path,
    )
    agent: Agent | None = None

    async def approve_for_eval(tool_name: str) -> bool:
        del tool_name
        return True

    async def start_agent() -> Agent:
        candidate = Agent(telemetry=telemetry, memory_path=memory_path)
        # Evaluations have no interactive terminal. This override is intentionally
        # confined to the eval-only subprocess and does not change Agent behavior.
        candidate._ask_permission = approve_for_eval  # type: ignore[method-assign]
        try:
            await candidate.start()
        except BaseException:
            await candidate.close()
            raise
        apply_agent_config(candidate, agent_config)
        return candidate

    try:
        with redirect_stdout(sys.stderr):
            agent = await start_agent()
            result = ""
            first_turn_for_agent = True
            for index, turn in enumerate(turns):
                result = await agent.run(turn, new_session=first_turn_for_agent)
                first_turn_for_agent = False
                turn_number = index + 1
                if turn_number in checkpoint_turns:
                    checkpoints[str(turn_number)] = capture_checkpoint(Path.cwd())
                    (runtime_root / "checkpoints.json").write_text(
                        json.dumps(checkpoints)
                    )
                if turn_number in restart_agent_after_turns:
                    await agent.close()
                    agent = None
                    agent = await start_agent()
                    first_turn_for_agent = True
            return result
    finally:
        try:
            if agent is not None:
                await agent.close()
        finally:
            telemetry.shutdown()


def main() -> None:
    """Read an evaluation payload and write only the final answer to stdout."""
    raw = sys.stdin.read()
    turns, agent_config, restart_agent_after_turns, runtime_root = parse_payload(raw)
    raw_checkpoints = json.loads(raw).get("checkpoint_turns", [])
    checkpoints = (
        validate_checkpoint_turns(raw_checkpoints, len(turns))
        if raw_checkpoints
        else ()
    )
    result = asyncio.run(
        run(
            turns,
            agent_config,
            restart_agent_after_turns,
            runtime_root=runtime_root,
            checkpoint_turns=checkpoints,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
