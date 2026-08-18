import asyncio

from code_agent.agent import Agent
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry


def main() -> None:
    """Run the interactive coding agent."""
    telemetry = AgentTelemetry.initialize(
        enabled=settings.phoenix_enabled,
        endpoint=settings.phoenix_collector_endpoint,
        project_name=settings.phoenix_project_name,
    )
    try:
        cli_agent = Agent(telemetry=telemetry)
        asyncio.run(cli_agent.start())
    except KeyboardInterrupt:
        print()
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    main()
