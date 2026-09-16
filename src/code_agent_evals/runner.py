"""Non-interactive subprocess entry point used only by Inspect evaluations."""

import asyncio
import json
import sys
from contextlib import redirect_stdout

from code_agent.agent import Agent
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry
from code_agent_evals.trace import TRACE_DIRECTORY

_SESSION_CONFIG_FIELDS = {
    "compact_thresh_hold",
    "max_tool_res",
    "max_tool_round_res",
    "persist_threshold",
    "reserved_token",
}


def parse_payload(raw: str) -> tuple[list[str], dict[str, int], tuple[int, ...]]:
    """Parse the solver payload while accepting legacy single-prompt input."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return [raw], {}, ()

    if not isinstance(payload, dict):
        return [raw], {}, ()

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

    return turns, agent_config, tuple(sorted(raw_restarts))


def apply_agent_config(agent: Agent, config: dict[str, int]) -> None:
    """Apply the allowlisted evaluation thresholds to the active session."""
    for name, value in config.items():
        setattr(agent.session, name, value)


async def run(
    turns: list[str],
    agent_config: dict[str, int],
    restart_agent_after_turns: tuple[int, ...] = (),
) -> str:
    """Run evaluation turns, optionally restarting Agent between episodes."""
    telemetry = AgentTelemetry.initialize(
        enabled=settings.phoenix_enabled,
        endpoint=settings.phoenix_collector_endpoint,
        project_name=settings.phoenix_project_name,
        trace_log_dir=TRACE_DIRECTORY,
    )
    agent: Agent | None = None

    async def approve_for_eval(tool_name: str) -> bool:
        del tool_name
        return True

    async def start_agent() -> Agent:
        candidate = Agent(telemetry=telemetry)
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
    turns, agent_config, restart_agent_after_turns = parse_payload(sys.stdin.read())
    result = asyncio.run(run(turns, agent_config, restart_agent_after_turns))
    print(result)


if __name__ == "__main__":
    main()
