from code_agent.context_manager import ContextManager


def tool_result(tool_call_id: str, content: str) -> dict[str, str]:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def test_tool_res_compact_persists_largest_current_round_results(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = ContextManager(persist_preview_chars=4)
    manager.max_tool_round_res = 10
    manager.persist_threshold = 6
    messages = [
        tool_result("old", "x" * 20),
        {"role": "assistant", "content": "next round"},
        tool_result("small", "a" * 5),
        tool_result("large", "你好世界啊"),
    ]

    result = manager._tool_res_compact(messages)

    assert result is messages
    assert messages[0]["content"] == "x" * 20
    assert messages[2]["content"] == "a" * 5
    assert messages[3]["content"].startswith("<tool-result-persisted>\n你好世界...")
    assert (tmp_path / "task_output/tool_results/large.txt").read_text() == "你好世界啊"


def test_tool_res_compact_stops_when_remaining_results_are_below_threshold(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = ContextManager()
    manager.max_tool_round_res = 5
    manager.persist_threshold = 4
    messages = [tool_result("one", "abc"), tool_result("two", "def")]

    manager._tool_res_compact(messages)

    assert [message["content"] for message in messages] == ["abc", "def"]
    assert not (tmp_path / "task_output").exists()


def test_tool_res_compact_counts_persisted_placeholder_bytes(monkeypatch) -> None:
    manager = ContextManager()
    manager.max_tool_round_res = 11
    manager.persist_threshold = 1
    messages = [tool_result("large", "a" * 10), tool_result("small", "b" * 8)]
    monkeypatch.setattr(manager, "_persist_large_output", lambda message: "done")

    manager._tool_res_compact(messages)

    assert [message["content"] for message in messages] == ["done", "done"]
