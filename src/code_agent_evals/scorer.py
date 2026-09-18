"""Scorers for file-editing agent evaluations."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import SandboxEnvironment, sandbox

from code_agent_evals.trace import EvaluationTrace, TraceFormatError, read_trace_input

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HIDDEN_TESTS_ROOT = _PROJECT_ROOT / "evals" / "hidden_tests"
_HIDDEN_TESTS_DESTINATION = PurePosixPath(".eval_hidden_tests")
_TEST_GROUPS = ("FAIL_TO_PASS", "PASS_TO_PASS")
_PRESERVE_CATEGORIES = {
    "constraint",
    "decision_update",
    "entity_binding",
    "tool_result",
}
_ACTIVATION_WEIGHT = 0.25
_PRESERVATION_WEIGHT = 0.25
_BEHAVIOR_WEIGHT = 0.50


@dataclass(frozen=True)
class EvaluationContract:
    """The regression and compatibility tests that define one SWE case."""

    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]


@dataclass(frozen=True)
class PreservationCheck:
    id: str
    category: str
    introduced_turn: int
    used_turn: int
    min_compact_hops: int
    summary_contains: tuple[str, ...]
    behavior_tests: tuple[str, ...]


def preservation_checks(
    metadata: dict[str, Any] | None,
) -> tuple[PreservationCheck, ...] | None:
    """Validate scorer-only context survival checks before reading trace input."""
    if not metadata or "context_survival" not in metadata:
        return None
    raw = metadata["context_survival"]
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("checks"), list)
        or not raw["checks"]
    ):
        raise ValueError("metadata.context_survival.checks must be a non-empty list")
    turns = metadata.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("context_survival requires metadata.turns")
    checks = []
    ids = set()
    for item in raw["checks"]:
        if not isinstance(item, dict):
            raise TypeError("context_survival checks must be objects")
        check_id = item.get("id")
        if not isinstance(check_id, str) or not check_id.strip() or check_id in ids:
            raise ValueError(
                "context_survival check ids must be unique non-empty strings"
            )
        ids.add(check_id)
        category = item.get("category")
        if category not in _PRESERVE_CATEGORIES:
            raise ValueError(f"invalid context_survival category: {category!r}")
        introduced = item.get("introduced_turn")
        used = item.get("used_turn")
        hops = item.get("min_compact_hops")
        if (
            any(type(value) is not int for value in (introduced, used, hops))
            or not (1 <= introduced < used <= len(turns))
            or hops < 1
        ):
            raise ValueError(
                "invalid context_survival turn window or compact hop count"
            )
        markers = item.get("summary_contains")
        if (
            not isinstance(markers, list)
            or not markers
            or any(
                not isinstance(marker, str) or not marker.strip() for marker in markers
            )
        ):
            raise ValueError("summary_contains must be a non-empty list of strings")
        selectors = item.get("behavior_tests")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError("behavior_tests must be a non-empty list")
        validated_selectors = tuple(
            _validated_selector(selector, "behavior_tests") for selector in selectors
        )
        if len(validated_selectors) != len(set(validated_selectors)):
            raise ValueError("behavior_tests contains duplicate selectors")
        checks.append(
            PreservationCheck(
                check_id,
                category,
                introduced,
                used,
                hops,
                tuple(markers),
                validated_selectors,
            )
        )
    return tuple(checks)


def preservation_trace_result(
    check: PreservationCheck, trace: EvaluationTrace
) -> tuple[bool, bool, int]:
    try:
        compacts = trace.compact_history_between(check.introduced_turn, check.used_turn)
    except (IndexError, ValueError) as error:
        raise TraceFormatError(
            f"trace lacks declared turn window for {check.id}"
        ) from error
    activated = len(compacts) >= check.min_compact_hops
    # Every required marker must survive each hop, including the last summary.
    preserved = activated and all(
        isinstance(span.output_value, str)
        and all(marker in span.output_value for marker in check.summary_contains)
        for span in compacts
    )
    return activated, preserved, len(compacts)


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
    checks = preservation_checks(state.metadata)
    if checks is not None:
        trace = await read_trace_input(environment)
        if any(
            selector.startswith(f"{_HIDDEN_TESTS_DESTINATION}/")
            for check in checks
            for selector in check.behavior_tests
        ):
            await _inject_hidden_tests(environment, state.sample_id)
        results = []
        explanations = []
        for check in checks:
            activated, preserved, compact_hops = preservation_trace_result(check, trace)
            tests = []
            for selector in check.behavior_tests:
                test_result = await _run_pytest(environment, (selector,))
                tests.append({"selector": selector, "passed": test_result.success})
                explanations.append(
                    f"{check.id} {selector}: {_test_output(test_result)}"
                )
            tests_passed = sum(test["passed"] for test in tests)
            behavior_passed = tests_passed == len(tests)
            behavior_score = _BEHAVIOR_WEIGHT * tests_passed / len(tests)
            check_score = (
                (_ACTIVATION_WEIGHT if activated else 0.0)
                + (_PRESERVATION_WEIGHT if preserved else 0.0)
                + behavior_score
            )
            results.append(
                {
                    "id": check.id,
                    "category": check.category,
                    "score": check_score,
                    "strict_pass": activated and preserved and behavior_passed,
                    "activation": {
                        "passed": activated,
                        "score": _ACTIVATION_WEIGHT if activated else 0.0,
                        "compact_hops": compact_hops,
                        "min_compact_hops": check.min_compact_hops,
                    },
                    "trace_preserved": {
                        "passed": preserved,
                        "score": _PRESERVATION_WEIGHT if preserved else 0.0,
                    },
                    "behavior": {
                        "passed": behavior_passed,
                        "score": behavior_score,
                        "tests_passed": tests_passed,
                        "tests_total": len(tests),
                        "tests": tests,
                    },
                }
            )
            explanations.append(
                f"{check.id}: score={check_score:.3f}, "
                f"strict_pass={activated and preserved and behavior_passed}, "
                f"activation={activated}, trace_preserved={preserved}, "
                f"behavior={tests_passed}/{len(tests)}"
            )
        overall_score = sum(result["score"] for result in results) / len(results)
        return Score(
            value=overall_score,
            explanation="\n\n".join(explanations)[-8000:],
            metadata={
                "context_survival": {
                    "strict_pass": all(result["strict_pass"] for result in results),
                    "checks": results,
                }
            },
        )
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
