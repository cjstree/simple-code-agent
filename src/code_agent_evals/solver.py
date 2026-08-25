import sys

from inspect_ai.agent import sandbox_agent_bridge
from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox


@solver
def my_agent_solver() -> Solver:
    async def solve(
        state: TaskState,
        generate: Generate,
    ) -> TaskState:
        del generate

        async with sandbox_agent_bridge(forward_generation_config=True) as bridge:
            result = await sandbox().exec(
                [sys.executable, "-m", "code_agent_evals.runner"],
                input=state.input_text,
                env={
                    "LLM_API_KEY": "inspect-eval",
                    "LLM_BASE_URL": f"http://localhost:{bridge.port}/v1",
                    "LLM_MODEL_NAME": "inspect",
                },
                timeout=300,
            )

        if not result.success:
            details = "\n".join(part for part in (result.stdout, result.stderr) if part)
            raise RuntimeError(f"Agent subprocess failed:\n{details}")

        state.output = ModelOutput.from_content(
            model="my-agent",
            content=result.stdout.strip(),
        )
        return state

    return solve
