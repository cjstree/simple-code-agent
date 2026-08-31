"""Non-interactive subprocess entry point used only by Inspect evaluations."""

import asyncio
import json
import sys
from contextlib import redirect_stdout

from code_agent.agent import Agent
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry

_SESSION_CONFIG_FIELDS = {
    "compact_thresh_hold",
    "max_tool_res",
    "max_tool_round_res",
    "persist_threshold",
    "reserved_token",
}


def parse_payload(raw: str) -> tuple[list[str], dict[str, int]]:
    """Parse the solver payload while accepting legacy single-prompt input."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return [raw], {}

    if not isinstance(payload, dict):
        return [raw], {}

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

    return turns, agent_config


def apply_agent_config(agent: Agent, config: dict[str, int]) -> None:
    """Apply the allowlisted evaluation thresholds to the active session."""
    for name, value in config.items():
        setattr(agent.session, name, value)


async def run(turns: list[str], agent_config: dict[str, int]) -> str:
    """Run all evaluation turns in one agent session."""
    telemetry = AgentTelemetry.initialize(
        enabled=settings.phoenix_enabled,
        endpoint=settings.phoenix_collector_endpoint,
        project_name=settings.phoenix_project_name,
    )
    agent = Agent(telemetry=telemetry)

    async def approve_for_eval(tool_name: str) -> bool:
        del tool_name
        return True

    # Evaluations have no interactive terminal. This override is intentionally
    # confined to the eval-only subprocess and does not change Agent behavior.
    agent._ask_permission = approve_for_eval  # type: ignore[method-assign]

    try:
        with redirect_stdout(sys.stderr):
            await agent.start()
            apply_agent_config(agent, agent_config)
            result = ""
            for index, turn in enumerate(turns):
                result = await agent.run(turn, new_session=index == 0)
            return result
    finally:
        try:
            await agent.close()
        finally:
            telemetry.shutdown()


def main() -> None:
    """Read an evaluation payload and write only the final answer to stdout."""
    turns, agent_config = parse_payload(sys.stdin.read())
    result = asyncio.run(run(turns, agent_config))
    print(result)


if __name__ == "__main__":
    main()
