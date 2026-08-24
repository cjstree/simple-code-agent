from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.scorer import match

from code_agent_evals.solver import my_agent_solver


@task
def basic_agent_eval():
    return Task(
        dataset=json_dataset("/home/cjs/agent/dataset/basic.jsonl"),
        solver=my_agent_solver(),
        scorer=match(),
    )
