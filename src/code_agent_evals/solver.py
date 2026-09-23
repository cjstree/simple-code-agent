import json
import shutil
import sys
import tempfile
from pathlib import Path

from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

from code_agent_evals.trace import (
    TRACE_STORE_KEY,
    read_trace_bundle,
)

_DEFAULT_RUNNER_TIMEOUT_SECONDS = 300


def _runner_payload(state: TaskState, runtime_root: Path) -> str:
    """Serialize one or more user turns for the evaluation runner."""
    metadata = state.metadata or {}
    turns = metadata.get("turns", [state.input_text])
    agent_config = metadata.get("agent_config", {})
    restart_agent_after_turns = metadata.get("restart_agent_after_turns", [])
    return json.dumps(
        {
            "turns": turns,
            "agent_config": agent_config,
            "restart_agent_after_turns": restart_agent_after_turns,
            "eval_runtime_root": str(runtime_root),
        },
        ensure_ascii=False,
    )


@solver
def my_agent_solver() -> Solver:
    async def solve(
        state: TaskState,
        generate: Generate,
    ) -> TaskState:
        del generate
        timeout = (state.metadata or {}).get(
            "runner_timeout_seconds", _DEFAULT_RUNNER_TIMEOUT_SECONDS
        )
        if type(timeout) is not int or timeout <= 0:
            raise ValueError("runner_timeout_seconds must be a positive integer")

        runtime_root = Path(tempfile.mkdtemp(prefix="code-agent-eval-")).resolve()
        primary_error: BaseException | None = None
        try:
            result = await sandbox().exec(
                [sys.executable, "-m", "code_agent_evals.runner"],
                input=_runner_payload(state, runtime_root),
                timeout=timeout,
            )

            if not result.success:
                details = "\n".join(
                    part for part in (result.stdout, result.stderr) if part
                )
                raise RuntimeError(f"Agent subprocess failed:\n{details}")

            state.store.set(TRACE_STORE_KEY, read_trace_bundle(runtime_root / "traces"))
            state.output = ModelOutput.from_content(
                model="my-agent",
                content=result.stdout.strip(),
            )
            return state
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                shutil.rmtree(runtime_root)
            except OSError as cleanup_error:
                if primary_error is not None:
                    primary_error.add_note(
                        f"failed to clean eval runtime root {runtime_root}: {cleanup_error}"
                    )
                else:
                    raise RuntimeError(
                        f"failed to clean eval runtime root {runtime_root}"
                    ) from cleanup_error

    return solve
