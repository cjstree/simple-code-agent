"""Scorers for file-editing agent evaluations."""

import sys

from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import sandbox


@scorer(metrics=[accuracy()])
def tests_pass() -> Scorer:
    """Score a sample by running its pytest suite in the sample workspace."""

    async def score(state: TaskState, target: Target) -> Score:
        del state, target
        result = await sandbox().exec(
            [sys.executable, "-m", "pytest", "-q"],
            timeout=60,
        )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        return Score(
            value=1 if result.success else 0,
            explanation=output[-4000:],
        )

    return score
