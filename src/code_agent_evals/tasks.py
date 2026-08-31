from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset

from code_agent_evals.scorer import tests_pass
from code_agent_evals.solver import my_agent_solver

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@task
def basic_agent_eval():
    return Task(
        dataset=json_dataset(str(_PROJECT_ROOT / "evals/file_edit.jsonl")),
        solver=my_agent_solver(),
        scorer=tests_pass(),
        sandbox="local",
    )
