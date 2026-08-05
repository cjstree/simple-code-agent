import asyncio

from code_agent.agent import Agent


def main() -> None:
    """Run the interactive coding agent."""
    cli_agent = Agent()
    try:
        asyncio.run(cli_agent.start())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
