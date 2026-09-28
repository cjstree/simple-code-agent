"""Checkpoint evidence and behavior-only scoring for long conversations."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from inspect_ai.scorer import Score

from code_agent_evals.trace import TraceFormatError

CHECKPOINT_STORE_KEY = "code_agent_eval.checkpoints.v1"


def checkpoint_turns(metadata: dict[str, Any]) -> tuple[int, ...]:
    contract = metadata.get("context_survival", {})
    profile = contract.get("scoring")
    if profile is None:
        return ()
    if profile != "long_horizon_v1":
        raise ValueError(f"unsupported context survival scoring: {profile}")
    return validate_checkpoint_turns(
        contract.get("checkpoint_turns"), len(metadata["turns"])
    )


def validate_checkpoint_turns(value: Any, total: int) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or len(value) < 2
        or any(type(turn) is not int or not 1 <= turn <= total for turn in value)
        or value != sorted(set(value))
        or value[-1] != total
    ):
        raise ValueError(
            "checkpoint_turns must be ordered unique turns ending at the final turn"
        )
    return tuple(value)


def capture_checkpoint(workspace: Path) -> dict[str, str]:
    """Capture Python sources, without caches, tests or evaluation artifacts."""
    files = {}
    for path in workspace.rglob("*.py"):
        relative = path.relative_to(workspace)
        if any(
            part.startswith(".") or part in {"tests", "__pycache__"}
            for part in relative.parts
        ):
            continue
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise RuntimeError(f"checkpoint source cannot be a symlink: {relative}")
        files[relative.as_posix()] = path.read_text(encoding="utf-8")
    return files


async def score_long_horizon(state, environment, checks, trace) -> Score:
    # Import here to keep the common atomic scorer independent of checkpoints.
    from code_agent_evals.scorer import (
        _HIDDEN_TESTS_ROOT,
        _PROJECT_ROOT,
        preservation_trace_result,
        source_tool_observed,
    )

    turns = checkpoint_turns(state.metadata)
    if any(check.introduced_turn >= turns[0] for check in checks):
        raise ValueError("all checks must be introduced before the first checkpoint")
    bundle = state.store.get(CHECKPOINT_STORE_KEY)
    if not isinstance(bundle, dict) or set(bundle) != {str(turn) for turn in turns}:
        raise TraceFormatError("missing or mismatched checkpoint evidence")
    sample_id = str(state.sample_id)
    if Path(sample_id).name != sample_id:
        raise ValueError("invalid checkpoint sample id")
    fixture = _PROJECT_ROOT / "evals" / "fixtures" / sample_id
    hidden = _HIDDEN_TESTS_ROOT / sample_id
    if not fixture.is_dir() or not hidden.is_dir():
        raise RuntimeError("checkpoint verification inputs are unavailable")
    trusted_tests = {
        f"tests/{p.relative_to(fixture / 'tests').as_posix()}": p.read_text()
        for p in (fixture / "tests").rglob("*.py")
    }
    trusted_tests.update(
        {
            f".eval_hidden_tests/{p.relative_to(hidden).as_posix()}": p.read_text()
            for p in hidden.rglob("*.py")
        }
    )
    stages = []
    previous: dict[str, bool] = {}
    regressions = []
    opportunities = 0
    # Fresh directories prevent stale modules or agent-edited tests from scoring.
    root = f".eval_checkpoints_{uuid.uuid4().hex}"
    for turn in turns:
        files = bundle[str(turn)]
        if not isinstance(files, dict):
            raise TraceFormatError("invalid checkpoint files")
        for name, content in files.items():
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.suffix != ".py"
                or any(part.startswith(".") or part == "tests" for part in path.parts)
                or not isinstance(content, str)
            ):
                raise TraceFormatError("unsafe checkpoint source")
        directory = f"{root}/{turn}"
        for name, content in {**files, **trusted_tests}.items():
            await environment.write_file(f"{directory}/{name}", content)
        results = []
        cache = {}
        for check in checks:
            outcomes = []
            for selector in check.behavior_tests:
                if selector not in cache:
                    result = await environment.exec(
                        [sys.executable, "-m", "pytest", "-q", selector],
                        cwd=directory,
                        env={"PYTHONPATH": "."},
                        timeout=60,
                    )
                    # Exit 2 can mean collection failed on broken agent code.
                    if result.returncode not in (0, 1, 2):
                        raise RuntimeError(
                            f"checkpoint verification failed: {result.stdout}\n{result.stderr}"
                        )
                    cache[selector] = {"selector": selector, "passed": result.success}
                    if not result.success:
                        cache[selector]["output"] = (
                            result.stdout + "\n" + result.stderr
                        )[-4000:]
                passed = cache[selector]["passed"]
                outcomes.append(cache[selector])
                key = f"{check.id}:{selector}"
                if previous.get(key, False):
                    opportunities += 1
                    if not passed:
                        regressions.append(
                            {"turn": turn, "check": check.id, "selector": selector}
                        )
                previous[key] = passed
            results.append(
                {
                    "id": check.id,
                    "category": check.category,
                    "score": sum(x["passed"] for x in outcomes) / len(outcomes),
                    "tests": outcomes,
                }
            )
        categories = {}
        for category in sorted({check.category for check in checks}):
            values = [item["score"] for item in results if item["category"] == category]
            categories[category] = sum(values) / len(values)
        stages.append(
            {
                "turn": turn,
                "score": sum(categories.values()) / len(categories),
                "categories": categories,
                "checks": results,
            }
        )
    diagnostics = []
    for check in checks:
        activated, preserved, hops = preservation_trace_result(check, trace)
        diagnostics.append(
            {
                "id": check.id,
                "compact_exposed": activated,
                "compact_hops": hops,
                "summary_markers_preserved": preserved,
                "source_tool_observed": source_tool_observed(check, trace),
            }
        )
    value = sum(stage["score"] for stage in stages) / len(stages)
    return Score(
        value=value,
        explanation=(
            "Long-horizon behavior (equal category/check/checkpoint weights): "
            + ", ".join(
                f"turn {stage['turn']}={stage['score']:.3f}" for stage in stages
            )
            + f"; pass-to-fail regressions={len(regressions)}. Compact evidence is diagnostic only."
        ),
        metadata={
            "context_survival": {
                "scoring": "long_horizon_v1",
                "strict_pass": value == 1,
                "checkpoints": stages,
                "final_score": stages[-1]["score"],
                "worst_checkpoint_score": min(stage["score"] for stage in stages),
                "drift_rate": len(regressions) / opportunities
                if opportunities
                else 0.0,
                "drift_opportunities": opportunities,
                "regressions": regressions,
                "compact_exposed": all(item["compact_exposed"] for item in diagnostics),
                "diagnostics": diagnostics,
            }
        },
    )
