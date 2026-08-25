from pathlib import Path

from code_agent_evals.tasks import basic_agent_eval


def test_basic_agent_eval_uses_isolated_file_fixture() -> None:
    task = basic_agent_eval()

    assert task.sandbox is not None
    assert task.sandbox.type == "local"
    assert len(task.dataset) == 2

    samples = {sample.id: sample for sample in task.dataset}
    sample = samples["fix_add"]
    assert sample.files is not None
    fixture = Path(sample.files["."])
    assert fixture.name == "fix_add"
    assert (fixture / "calculator.py").is_file()
    assert (fixture / "tests/test_calculator.py").is_file()


def test_context_fixture_includes_skills_and_memory() -> None:
    task = basic_agent_eval()
    samples = {sample.id: sample for sample in task.dataset}

    sample = samples["fix_multiply_with_context"]
    assert sample.files is not None
    fixture = Path(sample.files["."])

    assert (fixture / "calculator.py").is_file()
    assert (fixture / "tests/test_calculator.py").is_file()
    assert (fixture / "skills/arithmetic-fix/SKILL.md").is_file()
    assert (fixture / "memory/MEMORY.md").is_file()
    assert (fixture / "memory/project-arithmetic.md").is_file()
