import json
import sys

from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox


def _runner_payload(state: TaskState) -> str:
    """Serialize one or more user turns for the evaluation runner."""
    metadata = state.metadata or {}
    turns = metadata.get("turns", [state.input_text])
    agent_config = metadata.get("agent_config", {})
    return json.dumps(
        {"turns": turns, "agent_config": agent_config},
        ensure_ascii=False,
    )


@solver
def my_agent_solver() -> Solver:
    async def solve(
        state: TaskState,
        generate: Generate,
    ) -> TaskState:
        del generate

        result = await sandbox().exec(
            [sys.executable, "-m", "code_agent_evals.runner"],
            input=_runner_payload(state),
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
