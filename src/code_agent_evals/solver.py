from inspect_ai.agent import agent_bridge
from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

from code_agent.agent import Agent
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry


@solver
def my_agent_solver() -> Solver:

    async def solve(
        state: TaskState,
        generate: Generate,
    ) -> TaskState:
        telemetry = AgentTelemetry.initialize(
            enabled=settings.phoenix_enabled,
            endpoint=settings.phoenix_collector_endpoint,
            project_name=settings.phoenix_project_name,
        )
        agent = Agent(telemetry=telemetry)
        try:
            async with agent_bridge(forward_generation_config=True):
                await agent.start()
                result = await agent.run(state.input_text)
        except KeyboardInterrupt:
            print()
        finally:
            try:
                await agent.close()
            finally:
                telemetry.shutdown()

        state.output = ModelOutput.from_content(
            model="my-agent",
            content=result,
        )
        return state

    return solve
