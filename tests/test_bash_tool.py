import pytest

from code_agent.tool import BashTool


@pytest.mark.asyncio
async def test_bash_truncates_a_long_line_and_preserves_its_head_and_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One line larger than asyncio's line limit remains readable and is bounded.
    max_output_bytes = 1024
    monkeypatch.setattr("code_agent.tool.BASH_MAX_OUTPUT_BYTES", max_output_bytes)
    head = "start-of-output:"
    tail = ":end-of-output"
    body_size = 70 * 1024
    command = f"printf '%s%*s%s' '{head}' {body_size} '' '{tail}'"

    result = await BashTool().run({"cmd": command})

    omitted_bytes = len(head) + body_size + len(tail) - max_output_bytes
    assert result.startswith(head)
    assert result.endswith(tail)
    assert f"...({omitted_bytes} bytes truncated)..." in result
    assert len(result.encode()) < max_output_bytes + 100


@pytest.mark.asyncio
async def test_bash_returns_complete_output_within_limit() -> None:
    # Ordinary output below the cap is returned without a truncation marker.
    result = await BashTool().run({"cmd": "printf 'first line\\nlast line\\n'"})

    assert result == "first line\nlast line"
    assert "truncated" not in result
