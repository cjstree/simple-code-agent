"""Scorers for file-editing agent evaluations."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import SandboxEnvironment, sandbox

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HIDDEN_TESTS_ROOT = _PROJECT_ROOT / "evals" / "hidden_tests"
_HIDDEN_TESTS_DESTINATION = PurePosixPath(".eval_hidden_tests")
_TEST_GROUPS = ("FAIL_TO_PASS", "PASS_TO_PASS")


@dataclass(frozen=True)
class EvaluationContract:
    """The regression and compatibility tests that define one SWE case."""

    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]


def _validated_selector(value: Any, group: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"metadata.{group} entries must be non-empty strings")

    selector = value.strip()
    test_path = PurePosixPath(selector.split("::", 1)[0])
    if test_path.is_absolute() or ".." in test_path.parts:
        raise ValueError(f"metadata.{group} contains an unsafe test selector")
    if not test_path.parts or test_path.parts[0] not in {
        "tests",
        str(_HIDDEN_TESTS_DESTINATION),
    }:
        raise ValueError(
            f"metadata.{group} selectors must target tests/ or "
            f"{_HIDDEN_TESTS_DESTINATION}/"
        )
    return selector


def evaluation_contract(metadata: dict[str, Any] | None) -> EvaluationContract | None:
    """Parse an optional FAIL_TO_PASS/PASS_TO_PASS sample contract."""
    metadata = metadata or {}
    present_groups = {name for name in _TEST_GROUPS if name in metadata}
    if not present_groups:
        return None
    if present_groups != set(_TEST_GROUPS):
        missing = ", ".join(sorted(set(_TEST_GROUPS) - present_groups))
        raise ValueError(f"evaluation metadata is missing {missing}")

    groups: dict[str, tuple[str, ...]] = {}
    for name in _TEST_GROUPS:
        raw_selectors = metadata[name]
        if not isinstance(raw_selectors, list) or not raw_selectors:
            raise ValueError(f"metadata.{name} must be a non-empty list")
        selectors = tuple(_validated_selector(value, name) for value in raw_selectors)
        if len(selectors) != len(set(selectors)):
            raise ValueError(f"metadata.{name} contains duplicate selectors")
        groups[name] = selectors

    overlap = set(groups["FAIL_TO_PASS"]) & set(groups["PASS_TO_PASS"])
    if overlap:
        raise ValueError("FAIL_TO_PASS and PASS_TO_PASS must be disjoint")

    return EvaluationContract(
        fail_to_pass=groups["FAIL_TO_PASS"],
        pass_to_pass=groups["PASS_TO_PASS"],
    )


def _hidden_source(sample_id: int | str) -> Path:
    sample_name = str(sample_id)
    if not sample_name or Path(sample_name).name != sample_name:
        raise ValueError("sample id cannot be used as a hidden-test directory name")
    source = _HIDDEN_TESTS_ROOT / sample_name
    if not source.is_dir():
        raise FileNotFoundError(f"hidden tests not found for sample {sample_name}")
    return source


async def _inject_hidden_tests(
    environment: SandboxEnvironment, sample_id: int | str
) -> list[str]:
    source = _hidden_source(sample_id)
    copied: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(source)
        destination = (_HIDDEN_TESTS_DESTINATION / relative.as_posix()).as_posix()
        await environment.write_file(destination, path.read_bytes())
        copied.append(destination)
    if not copied:
        raise ValueError(f"hidden-test directory for sample {sample_id} is empty")
    return copied


async def _run_pytest(environment: SandboxEnvironment, selectors: tuple[str, ...] = ()):
    return await environment.exec(
        [sys.executable, "-m", "pytest", "-q", *selectors],
        timeout=60,
    )


def _test_output(result: Any) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


async def score_tests(state: TaskState, environment: SandboxEnvironment) -> Score:
    """Score legacy pytest suites or an explicit mini-SWE test contract."""
    contract = evaluation_contract(state.metadata)
    if contract is None:
        result = await _run_pytest(environment)
        return Score(
            value=1 if result.success else 0,
            explanation=_test_output(result)[-4000:],
        )

    copied = await _inject_hidden_tests(environment, state.sample_id)
    fail_to_pass = await _run_pytest(environment, contract.fail_to_pass)
    pass_to_pass = await _run_pytest(environment, contract.pass_to_pass)
    successful = fail_to_pass.success and pass_to_pass.success
    explanation = (
        "FAIL_TO_PASS:\n"
        f"{_test_output(fail_to_pass)}\n\n"
        "PASS_TO_PASS:\n"
        f"{_test_output(pass_to_pass)}"
    )
    return Score(
        value=1 if successful else 0,
        explanation=explanation[-8000:],
        metadata={
            "FAIL_TO_PASS": "passed" if fail_to_pass.success else "failed",
            "PASS_TO_PASS": "passed" if pass_to_pass.success else "failed",
            "hidden_tests": copied,
        },
    )


@scorer(metrics=[accuracy()])
def tests_pass() -> Scorer:
    """Score externally observable test outcomes in the sample workspace."""

    async def score(state: TaskState, target: Target) -> Score:
        del target
        return await score_tests(state, sandbox())

    return score
