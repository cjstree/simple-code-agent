from pathlib import Path

import pytest
from pydantic import ValidationError

from code_agent.tool import READ_MAX_BYTES, READ_MAX_LINES, ReadTool
from code_agent.tool_registry import ToolRegistry


@pytest.mark.asyncio
async def test_read_uses_one_based_offset_and_reports_remaining_lines(
    tmp_path: Path,
) -> None:
    # A bounded read exposes numbered lines and an exact continuation offset.
    path = tmp_path / "sample.txt"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = await ReadTool().run({"path": str(path), "offset": 2, "limit": 2})

    assert result.startswith("   2| two\n   3| three")
    assert "[1 more line in file. Use offset=4 to continue.]" in result


@pytest.mark.asyncio
async def test_read_truncates_at_default_line_limit(tmp_path: Path) -> None:
    # An unbounded call cannot place more than the hard line limit in context.
    path = tmp_path / "many-lines.txt"
    path.write_text(
        "\n".join(f"line {number}" for number in range(1, READ_MAX_LINES + 2)),
        encoding="utf-8",
    )

    result = await ReadTool().run({"path": str(path)})

    assert f"{READ_MAX_LINES:4}| line {READ_MAX_LINES}" in result
    assert f"line {READ_MAX_LINES + 1}" not in result
    assert (
        f"[Showing lines 1-{READ_MAX_LINES} of {READ_MAX_LINES + 1}. "
        f"Use offset={READ_MAX_LINES + 1} to continue.]"
    ) in result


@pytest.mark.asyncio
async def test_read_truncates_at_utf8_byte_limit(tmp_path: Path) -> None:
    # Generated multibyte lines stop the result at the UTF-8 byte boundary.
    path = tmp_path / "many-bytes.txt"
    line = "界" * 100
    path.write_text("\n".join(line for _ in range(300)), encoding="utf-8")

    result = await ReadTool().run({"path": str(path)})

    content, notice = result.rsplit("\n\n", maxsplit=1)
    returned_lines = content.splitlines()
    assert 0 < len(returned_lines) < 300
    assert (
        sum(len(line.encode("utf-8")) + 1 for line in returned_lines) < READ_MAX_BYTES
    )
    assert "50.0KB limit" in notice
    assert f"Use offset={len(returned_lines) + 1} to continue." in notice


@pytest.mark.asyncio
async def test_read_does_not_return_a_partial_oversized_first_line(
    tmp_path: Path,
) -> None:
    # A generated single line larger than the byte cap yields an actionable fallback.
    path = tmp_path / "single-line.txt"
    path.write_text("x" * (READ_MAX_BYTES + 1), encoding="utf-8")

    result = await ReadTool().run({"path": str(path)})

    assert "Line 1 is 50.0KB" in result
    assert "exceeds 50.0KB limit" in result
    assert "Use bash: sed -n '1p'" in result
    assert "x" * 100 not in result


@pytest.mark.asyncio
async def test_read_rejects_invalid_ranges_and_offsets_past_eof(
    tmp_path: Path,
) -> None:
    # Invalid cursors fail explicitly instead of returning a misleading empty page.
    path = tmp_path / "short.txt"
    path.write_text("one\ntwo", encoding="utf-8")
    tool = ReadTool()

    with pytest.raises(ValidationError):
        await tool.run({"path": str(path), "offset": 0})
    with pytest.raises(ValidationError):
        await tool.run({"path": str(path), "limit": 0})
    with pytest.raises(ValueError, match=r"Offset 3 is beyond end of file"):
        await tool.run({"path": str(path), "offset": 3})


def test_read_registry_description_exposes_pagination_contract() -> None:
    # Tool discovery tells the model both the hard limits and cursor semantics.
    function = ToolRegistry(tools=[ReadTool()]).get_tools_desc()[0]["function"]

    assert "2000 lines or 50KB" in function["description"]
    assert "offset is 1-indexed" in function["description"]
    assert function["parameters"]["properties"]["offset"]["minimum"] == 1
    limit_schema = function["parameters"]["properties"]["limit"]
    integer_schema = next(
        option for option in limit_schema["anyOf"] if option.get("type") == "integer"
    )
    assert integer_schema["minimum"] == 1
