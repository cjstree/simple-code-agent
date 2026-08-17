import asyncio
import shlex
import sys
from pathlib import Path

import pytest

from code_agent.background_manager import BackgroundManager


def _python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


async def _wait_for_results(
    manager: BackgroundManager,
    count: int,
    *,
    timeout: float = 3,
) -> list[list[str]]:
    results: list[list[str]] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while len(results) < count and asyncio.get_running_loop().time() < deadline:
        results.extend(manager.collect())
        if len(results) < count:
            await asyncio.sleep(0.01)
    assert len(results) == count, f"expected {count} results, got {results!r}"
    return results


# A successful command should run in the background and expose its output once.
# Collecting is non-blocking and consumes results, so a second call returns empty.
@pytest.mark.asyncio
async def test_successful_command_is_collected_once() -> None:
    manager = BackgroundManager(max_process=1, time_out=2)
    try:
        task_id = manager.start(_python_command("print('hello from background')"))

        assert task_id
        assert manager.collect() == []
        assert await _wait_for_results(manager, 1) == [
            [task_id, "[succeeded]\nhello from background"],
        ]
        assert manager.collect() == []
    finally:
        await manager.close()


# With one process slot, a second command must remain queued behind the first.
# Completing the first command should automatically schedule the queued command.
@pytest.mark.asyncio
async def test_process_limit_queues_and_then_schedules_next_command(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    second_started = tmp_path / "second-started"
    first_source = (
        "import pathlib, time; "
        f"release = pathlib.Path({str(release)!r}); "
        "\nwhile not release.exists(): time.sleep(0.01)\n"
        "print('first finished')"
    )
    second_source = (
        "import pathlib; "
        f"pathlib.Path({str(second_started)!r}).write_text('started'); "
        "print('second finished')"
    )
    manager = BackgroundManager(max_process=1, time_out=2)
    try:
        first_id = manager.start(_python_command(first_source))
        second_id = manager.start(_python_command(second_source))

        await asyncio.sleep(0.1)
        assert not second_started.exists()
        release.write_text("go")

        results = await _wait_for_results(manager, 2)
        assert [task_id for task_id, _ in results] == [first_id, second_id]
        assert results[0][1] == "[succeeded]\nfirst finished"
        assert results[1][1] == "[succeeded]\nsecond finished"
    finally:
        await manager.close()


# A command that exits normally with a non-zero code is a completed failure.
# Its output and exit code should both be preserved in the collected result.
@pytest.mark.asyncio
async def test_nonzero_exit_is_reported_as_failure() -> None:
    manager = BackgroundManager(max_process=1, time_out=2)
    try:
        task_id = manager.start(
            _python_command("print('bad command'); raise SystemExit(7)")
        )

        assert await _wait_for_results(manager, 1) == [
            [task_id, "[failed: exit code 7]\nbad command"],
        ]
    finally:
        await manager.close()


# Timing out a shell command must terminate descendants in the same process group.
# The child marker proves that a surviving descendant was not left behind.
@pytest.mark.asyncio
async def test_timeout_reports_result_and_terminates_process_group(
    tmp_path: Path,
) -> None:
    child_marker = tmp_path / "child-finished"
    child_script = tmp_path / "child.py"
    parent_script = tmp_path / "parent.py"
    child_script.write_text(
        "import pathlib, time\n"
        "time.sleep(0.5)\n"
        f"pathlib.Path({str(child_marker)!r}).write_text('survived')\n"
    )
    parent_script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child_script)!r}])\n"
        "print('parent started', flush=True)\n"
        "time.sleep(10)\n"
    )
    manager = BackgroundManager(
        max_process=1,
        time_out=0.1,
        terminate_grace_period=0.05,
    )
    try:
        task_id = manager.start(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(parent_script))}"
        )

        results = await _wait_for_results(manager, 1)
        assert results == [[task_id, "[timed out after 0.1s]\nparent started"]]
        await asyncio.sleep(0.55)
        assert not child_marker.exists()
    finally:
        await manager.close()

'''
# Closing a manager should promptly stop running work and be safe to call twice.
# Once closed, the manager must reject commands instead of creating new workers.
@pytest.mark.asyncio
async def test_close_stops_work_and_rejects_new_commands(tmp_path: Path) -> None:
    completion_marker = tmp_path / "completed"
    source = (
        "import pathlib, time; "
        "time.sleep(0.5); "
        f"pathlib.Path({str(completion_marker)!r}).write_text('completed')"
    )
    manager = BackgroundManager(max_process=1, time_out=2)
    manager.start(_python_command(source))
    await asyncio.sleep(0.05)

    await asyncio.wait_for(manager.close(), timeout=1)
    await manager.close()
    await asyncio.sleep(0.55)

    assert not completion_marker.exists()
    assert manager.collect() == []
    with pytest.raises(RuntimeError, match="closed"):
        manager.start(_python_command("print('too late')"))
'''

# Formatting is independent from process control and covers the status variants.
# Long output keeps a bounded prefix and receives an explicit truncation marker.
@pytest.mark.parametrize(
    ("kwargs", "expected_status"),
    [
        ({"returncode": 0}, "[succeeded]"),
        ({"returncode": 4}, "[failed: exit code 4]"),
        ({"timed_out": True, "timeout": 1.5}, "[timed out after 1.5s]"),
        (
            {"error": OSError("cannot spawn")},
            "[failed to start: OSError: cannot spawn]",
        ),
    ],
)
def test_format_result_describes_status_and_truncates_output(
    kwargs: dict[str, object],
    expected_status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackgroundManager()
    monkeypatch.setattr(manager, "MAX_OUTPUT_CHARS", 5)

    result = manager._format_res("abcdefgh", **kwargs)  # type: ignore[arg-type]

    assert result == f"{expected_status}\nabcde{manager.TRUNCATION_MARKER}"
