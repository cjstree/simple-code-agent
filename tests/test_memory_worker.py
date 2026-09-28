import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from code_agent.agent import Agent
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


@pytest.mark.asyncio
async def test_queued_extractions_keep_their_triggering_turn(tmp_path) -> None:
    # Later queued work belongs to its own turn, and shutdown work belongs to the session.
    pytest.importorskip("opentelemetry.sdk")
    pytest.importorskip("openinference.instrumentation")
    pytest.importorskip("openinference.instrumentation.openai")
    telemetry = AgentTelemetry.initialize(
        enabled=False,
        endpoint="http://localhost:6006/v1/traces",
        project_name="test-project",
        trace_log_dir=tmp_path / "traces",
    )

    class EmptyCompletions:
        async def create(self, **request: Any) -> Any:
            del request
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=EmptyCompletions()))
    memory = Memory(path=tmp_path / "memory", client=client, telemetry=telemetry)
    agent = Agent(telemetry=telemetry, system_prompt="system")
    agent.session_id = "session-1"
    agent.memory = memory
    try:
        with telemetry.trace_turn(session_id="session-1", prompt="first"):
            agent.append_message({"role": "user", "content": "first"})
            agent.create_extract_task()
        with telemetry.trace_turn(session_id="session-1", prompt="second"):
            agent.append_message({"role": "user", "content": "second"})
            agent.create_extract_task()
        await agent.close()
    finally:
        telemetry.shutdown()

    records = [
        json.loads(line)
        for line in (tmp_path / "traces" / "trace_log_session-1.jsonl")
        .read_text()
        .splitlines()
    ]
    turns = {
        record["attributes"]["input.value"]: record["context"]["span_id"]
        for record in records
        if record["name"] == "agent.turn"
    }
    extracts = [record for record in records if record["name"] == "memory.extract"]
    assert len(extracts) == 3
    assert [record["parent_id"] for record in extracts] == [
        turns["first"], turns["second"], None
    ]
    assert [
        json.loads(record["attributes"]["input.value"])[-1]["content"]
        for record in extracts
    ] == ["first", "second", "second"]
    assert all(
        record["attributes"]["session.id"] == "session-1"
        for record in extracts
    )
