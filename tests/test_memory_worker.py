import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from code_agent.memory import Memory
from code_agent.telemetry import AgentTelemetry


@pytest.mark.asyncio
async def test_memory_persists_queued_snapshots_in_submission_order(tmp_path) -> None:
    # A slow older model response cannot overwrite a newer memory after it finishes.
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls: list[str] = []

    class FakeCompletions:
        async def create(self, **request: Any) -> Any:
            prompt = request["messages"][0]["content"]
            version = "old" if "user: old" in prompt else "new"
            calls.append(version)
            if version == "old":
                first_started.set()
                await release_first.wait()
            response = [{
                "name": "project-rule",
                "type": "project",
                "description": "Project rule",
                "body": version,
            }]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response)))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    memory = Memory(path=tmp_path, client=client, telemetry=AgentTelemetry())
    memory.schedule_extract([{"role": "user", "content": "old"}])
    memory.schedule_extract([{"role": "user", "content": "new"}])

    await asyncio.wait_for(first_started.wait(), timeout=1)
    assert calls == ["old"]
    release_first.set()

    assert await memory.close(timeout=1) is True
    assert await memory.close(timeout=1) is True
    assert calls == ["old", "new"]
    assert (tmp_path / "project-rule.md").read_text().endswith("\n\nnew\n")
    with pytest.raises(RuntimeError, match="closed"):
        memory.schedule_extract([{"role": "user", "content": "later"}])


@pytest.mark.asyncio
async def test_memory_timeout_cancels_running_task_and_discards_queue(tmp_path) -> None:
    # Shutdown may leave queued snapshots unfinished, but no worker remains running.
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls: list[str] = []

    class SlowCompletions:
        async def create(self, **request: Any) -> Any:
            calls.append(request["messages"][0]["content"])
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SlowCompletions()))
    memory = Memory(path=tmp_path, client=client, telemetry=AgentTelemetry())
    memory.schedule_extract([{"role": "user", "content": "first"}])
    memory.schedule_extract([{"role": "user", "content": "second"}])
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await memory.close(timeout=0.01) is False
    assert await memory.close(timeout=0.01) is False
    assert cancelled.is_set()
    assert len(calls) == 1
    assert "user: first" in calls[0]
    assert memory._extract_task is not None and memory._extract_task.done()
