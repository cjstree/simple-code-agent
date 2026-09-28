import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_agent_evals.long_horizon import (
    CHECKPOINT_STORE_KEY,
    capture_checkpoint,
    checkpoint_turns,
    score_long_horizon,
)
from code_agent_evals.scorer import preservation_checks
from code_agent_evals.solver import _runner_payload
from code_agent_evals.tasks import context_survival_eval
from code_agent_evals.trace import TraceFormatError


def mixed_sample():
    return next(
        s
        for s in context_survival_eval().dataset
        if s.id == "mixed_release_long_horizon"
    )


class LocalEnvironment:
    def __init__(self, root):
        self.root = root

    async def write_file(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    async def exec(self, command, cwd=None, env=None, **kwargs):
        result = subprocess.run(  # noqa: ASYNC221 - sequential local test adapter
            command,
            cwd=self.root / cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            check=False,
        )
        return SimpleNamespace(
            success=result.returncode == 0,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


@pytest.mark.asyncio
async def test_mixed_reference_and_drift_use_real_checkpoint_behavior(tmp_path):
    # A correct first/final state cannot conceal an intervening hotfix regression.
    sample = mixed_sample()
    checks = preservation_checks(sample.metadata)
    assert len(checks) == 8
    assert len({check.category for check in checks}) == 4
    workspace = tmp_path / "source"
    shutil.copytree(sample.files["."], workspace)
    baseline = subprocess.run(  # noqa: ASYNC221 - local fixture verification
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        env={**os.environ, "PYTHONPATH": str(workspace)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert baseline.returncode == 1, baseline.stdout + baseline.stderr
    project = Path(__file__).resolve().parents[1]
    patch = project / "evals" / sample.metadata["reference_patch"]
    subprocess.run(  # noqa: ASYNC221 - local fixture preparation
        ["git", "apply", str(patch)], cwd=workspace, check=True
    )
    good = capture_checkpoint(workspace)
    bad = dict(good)
    bad["release_policy.py"] = bad["release_policy.py"].replace(
        '"emergency"', '"pilot"'
    )
    bundle = {"12": good, "18": bad, "24": good}
    state = SimpleNamespace(
        sample_id=sample.id,
        metadata=sample.metadata,
        store={CHECKPOINT_STORE_KEY: bundle},
    )
    # The trace boundary deliberately contains no compaction: it must not affect the score.
    trace = SimpleNamespace(
        compact_history_between=lambda start, end: (),
        turn=lambda n: SimpleNamespace(tool_spans=()),
    )
    score = await score_long_horizon(state, LocalEnvironment(tmp_path), checks, trace)
    report = score.metadata["context_survival"]
    assert report["final_score"] == 1
    assert report["checkpoints"][0]["score"] == 1
    assert report["checkpoints"][1]["score"] < 1
    assert 0 < score.value < 1
    assert report["strict_pass"] is False
    assert report["compact_exposed"] is False
    assert report["drift_rate"] > 0
    assert {(r["turn"], r["check"]) for r in report["regressions"]} == {
        (18, "D-21.rev2")
    }
    assert score.value == pytest.approx(
        sum(s["score"] for s in report["checkpoints"]) / 3
    )
    payload = json.loads(
        _runner_payload(
            SimpleNamespace(metadata=sample.metadata, input_text=sample.input), tmp_path
        )
    )
    assert payload["checkpoint_turns"] == [12, 18, 24]
    assert "context_survival" not in payload


@pytest.mark.parametrize(
    "turns", [[12, 12, 24], [18, 12, 24], [12, 23], [True, 24], [], [12, 25]]
)
def test_checkpoint_contract_rejects_invalid_schedule(turns):
    # Invalid schedules must fail before starting an expensive model run.
    metadata = dict(mixed_sample().metadata)
    metadata["context_survival"] = {
        **metadata["context_survival"],
        "checkpoint_turns": turns,
    }
    with pytest.raises(ValueError, match="checkpoint_turns"):
        checkpoint_turns(metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bundle",
    [None, {"12": {}, "24": {}}, {"12": {"../escape.py": ""}, "18": {}, "24": {}}],
)
async def test_missing_or_unsafe_checkpoint_is_infrastructure_error(tmp_path, bundle):
    # Missing evidence and escaping paths cannot become ordinary task failures.
    sample = mixed_sample()
    state = SimpleNamespace(
        sample_id=sample.id,
        metadata=sample.metadata,
        store={CHECKPOINT_STORE_KEY: bundle},
    )
    with pytest.raises(TraceFormatError):
        await score_long_horizon(
            state,
            LocalEnvironment(tmp_path),
            preservation_checks(sample.metadata),
            None,
        )
    assert not (tmp_path / "escape.py").exists()
