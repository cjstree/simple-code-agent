import asyncio

from code_agent.agent import Agent
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry


async def _run_cli() -> None:
    """Run the interactive coding agent."""
    telemetry = AgentTelemetry.initialize(
        enabled=settings.phoenix_enabled,
        endpoint=settings.phoenix_collector_endpoint,
        project_name=settings.phoenix_project_name,
    )
    cli_agent = Agent(telemetry=telemetry)
    try:
        await cli_agent.start()
        await cli_agent.cli_loop()
    except KeyboardInterrupt:
        print()
    finally:
        try:
            await cli_agent.close()
        finally:
            telemetry.shutdown()


def main() -> None:
    """Run the asynchronous CLI from a synchronous console entry point."""
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
