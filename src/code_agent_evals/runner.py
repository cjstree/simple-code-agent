"""Non-interactive subprocess entry point used only by Inspect evaluations."""

import asyncio
import sys
from contextlib import redirect_stdout

from code_agent.agent import Agent
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry


async def run(prompt: str) -> str:
    """Run the production agent once while keeping eval output machine-readable."""
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
            return await agent.run(prompt)
    finally:
        try:
            await agent.close()
        finally:
            telemetry.shutdown()


def main() -> None:
    """Read one prompt from stdin and write only the final answer to stdout."""
    result = asyncio.run(run(sys.stdin.read()))
    print(result)


if __name__ == "__main__":
    main()
