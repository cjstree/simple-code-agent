import json
from types import SimpleNamespace
from typing import Any

import pytest

from code_agent.memory import Memory


class FakeCompletions:
    def __init__(self, *responses: str) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=next(self._responses)))
            ]
        )


class FakeClient:
    def __init__(self, *responses: str) -> None:
        self.completions = FakeCompletions(*responses)
        self.chat = SimpleNamespace(completions=self.completions)


def memory_file(name: str, description: str, mem_type: str, body: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"type: {mem_type}\n"
        "---\n\n"
        f"{body}\n"
    )


# Scenario: writing a memory creates its Markdown file and refreshes the catalog.
def test_write_memory_file_creates_file_and_catalog(tmp_path) -> None:
    memory_dir = tmp_path / "memories"
    memory = Memory(memory_dir, FakeClient())  # type: ignore[arg-type]

    memory.write_memory_file(
        name="User Preference Tabs",
        mem_type="user",
        description="User prefers tabs for indentation",
        body="Use tabs when editing files.",
    )

    assert (memory_dir / "user-preference-tabs.md").read_text(
        encoding="utf-8"
    ) == memory_file(
        "User Preference Tabs",
        "User prefers tabs for indentation",
        "user",
        "Use tabs when editing files.",
    )
    assert (memory_dir / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [0]:[User Preference Tabs](user-preference-tabs.md) — "
        "User prefers tabs for indentation\n"
    )


# Scenario: rebuilding the catalog sorts memory files and excludes MEMORY.md.
def test_rebuild_index_is_sorted_and_excludes_catalog(tmp_path) -> None:
    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    (memory_dir / "zeta.md").write_text(
        memory_file("Zeta", "Second entry", "project", "Zeta body"),
        encoding="utf-8",
    )
    (memory_dir / "alpha.md").write_text(
        memory_file("Alpha", "First entry", "user", "Alpha body"),
        encoding="utf-8",
    )
    (memory_dir / "MEMORY.md").write_text("stale catalog", encoding="utf-8")
    memory = Memory(memory_dir, FakeClient())  # type: ignore[arg-type]

    memory._rebuild_index()

    assert (memory_dir / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [0]:[Alpha](alpha.md) — First entry\n- [1]:[Zeta](zeta.md) — Second entry\n"
    )


# Scenario: selected indices return memory bodies in the model-provided order.
@pytest.mark.asyncio
async def test_select_relevant_memories_returns_bodies_in_selected_order(
    tmp_path,
) -> None:
    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    (memory_dir / "alpha.md").write_text(
        memory_file("Alpha", "First entry", "user", "Alpha body"),
        encoding="utf-8",
    )
    (memory_dir / "zeta.md").write_text(
        memory_file("Zeta", "Second entry", "project", "Zeta body"),
        encoding="utf-8",
    )
    client = FakeClient("[1, 0]")
    memory = Memory(memory_dir, client)  # type: ignore[arg-type]
    memory._rebuild_index()

    selected = await memory.select_relevant_memories(
        [{"role": "user", "content": "What conventions should I follow?"}],
        max_items=2,
    )

    assert selected == ["Zeta body", "Alpha body"]
    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    assert request["messages"][0]["role"] == "user"
    assert "What conventions should I follow?" in request["messages"][0]["content"]
    assert "[0]:[Alpha](alpha.md)" in request["messages"][0]["content"]


# Scenario: selection returns no more memory bodies than max_items permits.
@pytest.mark.asyncio
async def test_select_relevant_memories_respects_max_items(tmp_path) -> None:
    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    (memory_dir / "alpha.md").write_text(
        memory_file("Alpha", "First entry", "user", "Alpha body"),
        encoding="utf-8",
    )
    (memory_dir / "zeta.md").write_text(
        memory_file("Zeta", "Second entry", "project", "Zeta body"),
        encoding="utf-8",
    )
    client = FakeClient("[1, 0]")
    memory = Memory(memory_dir, client)  # type: ignore[arg-type]
    memory._rebuild_index()

    selected = await memory.select_relevant_memories([], max_items=1)

    assert selected == ["Zeta body"]


# Scenario: missing storage or an invalid model response yields no memories.
@pytest.mark.asyncio
async def test_select_relevant_memories_returns_empty_for_missing_or_invalid_data(
    tmp_path,
) -> None:
    missing_client = FakeClient()
    missing_memory = Memory(
        tmp_path / "missing",
        missing_client,  # type: ignore[arg-type]
    )

    assert await missing_memory.select_relevant_memories([]) == []
    assert missing_client.completions.calls == []

    memory_dir = tmp_path / "invalid"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text(
        "- [0]:[Alpha](alpha.md) — First entry\n", encoding="utf-8"
    )
    invalid_client = FakeClient("not valid JSON")
    invalid_memory = Memory(memory_dir, invalid_client)  # type: ignore[arg-type]

    assert await invalid_memory.select_relevant_memories([]) == []


# Scenario: extraction considers the latest 30 messages and persists new memories.
@pytest.mark.asyncio
async def test_extract_memories_uses_recent_dialogue_and_writes_results(
    tmp_path,
) -> None:
    extracted = [
        {
            "name": "Python Version",
            "type": "project",
            "description": "Project uses Python 3.11 or newer",
            "body": "Use Python 3.11+ syntax.",
        }
    ]
    client = FakeClient(json.dumps(extracted))
    memory_dir = tmp_path / "memories"
    memory = Memory(memory_dir, client)  # type: ignore[arg-type]
    messages = [{"role": "user", "content": f"dialogue-{index}"} for index in range(32)]

    await memory.extract_memories(messages)

    assert len(client.completions.calls) == 1
    prompt = client.completions.calls[0]["messages"][0]["content"]
    assert "user: dialogue-0\n" not in prompt
    assert "user: dialogue-1\n" not in prompt
    assert "dialogue-2" in prompt
    assert "dialogue-31" in prompt
    assert (memory_dir / "python-version.md").read_text(
        encoding="utf-8"
    ) == memory_file(
        "Python Version",
        "Project uses Python 3.11 or newer",
        "project",
        "Use Python 3.11+ syntax.",
    )


# Scenario: an empty extraction result does not create any memory files.
@pytest.mark.asyncio
async def test_extract_memories_does_not_write_when_nothing_is_extracted(
    tmp_path,
) -> None:
    client = FakeClient("[]")
    memory_dir = tmp_path / "memories"
    memory = Memory(memory_dir, client)  # type: ignore[arg-type]

    await memory.extract_memories([{"role": "user", "content": "hello"}])

    assert len(client.completions.calls) == 1
    assert not list(memory_dir.glob("*.md"))
