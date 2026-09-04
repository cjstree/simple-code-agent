"""Local tools and their shared interface."""

import asyncio
import glob as globlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, Field

from code_agent.background_manager import BackgroundManager
from code_agent.skill_registry import SkillRegistry

_RESET = "\033[0m"
_DIM = "\033[2m"

READ_MAX_LINES = 2000
READ_MAX_BYTES = 50 * 1024


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _render_read_page(
    lines: list[str],
    *,
    start_line: int,
) -> tuple[list[str], Literal["lines", "bytes"] | None]:
    rendered: list[str] = []
    rendered_bytes = 0

    for index, line in enumerate(lines):
        if len(rendered) >= READ_MAX_LINES:
            return rendered, "lines"

        numbered_line = f"{start_line + index:4}| {line}"
        line_bytes = len(numbered_line.encode("utf-8"))
        if rendered:
            line_bytes += 1
        if rendered_bytes + line_bytes > READ_MAX_BYTES:
            return rendered, "bytes"

        rendered.append(numbered_line)
        rendered_bytes += line_bytes

    return rendered, None


class Tool(Protocol):
    type: str
    name: str
    description: str
    parameters: dict[str, Any]

    async def run(self, arguments: dict[str, Any]) -> str: ...


TodoStatus = Literal["pending", "in_progress", "completed"]


# single todo item
class TodoItem(BaseModel):
    content: str
    status: TodoStatus


class TodoWriteArguments(BaseModel):
    todos: list[TodoItem]


class ReadArguments(BaseModel):
    path: str
    offset: int = Field(
        default=1,
        ge=1,
        description="Line number to start reading from (1-indexed)",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of lines to read",
    )


class TodoWriteTool:
    type = "function"
    name = "todo_write"
    description = "Create and manage a task list. Upload entire task list every time."
    arguments_model = TodoWriteArguments
    parameters: ClassVar[dict[str, Any]] = TodoWriteArguments.model_json_schema()

    async def run(self, arguments: dict[str, Any]) -> str:
        todo_list = self.arguments_model.model_validate(arguments).todos
        lines = []
        for todo in todo_list:
            icon = {"pending": " ", "in_progress": "▸", "completed": "✓"}[todo.status]
            lines.append(f"  [{icon}] {todo.content}")
        print("\n".join(lines))
        serialized = [todo.model_dump() for todo in todo_list]
        return f"Updated todo_list: {json.dumps(serialized, ensure_ascii=False)}"


class ReadTool:
    type = "function"
    name = "read"
    description = (
        "Read a text file with line numbers (file path, not directory). Output is "
        f"truncated to {READ_MAX_LINES} lines or {READ_MAX_BYTES // 1024}KB, "
        "whichever is reached first. offset is 1-indexed. Use offset/limit for "
        "large files and continue with the offset given in a truncated result."
    )
    parameters: ClassVar[dict[str, Any]] = ReadArguments.model_json_schema()
    arguments_model = ReadArguments

    async def run(self, arguments: dict[str, Any]) -> str:
        args = self.arguments_model.model_validate(arguments)
        text = await asyncio.to_thread(Path(args.path).read_text, encoding="utf-8")
        lines = text.splitlines()
        total_lines = len(lines)
        start_index = args.offset - 1

        if start_index >= total_lines:
            if total_lines == 0 and start_index == 0:
                return ""
            raise ValueError(
                f"Offset {args.offset} is beyond end of file "
                f"({total_lines} lines total)"
            )

        end_index = (
            min(start_index + args.limit, total_lines)
            if args.limit is not None
            else total_lines
        )
        candidate_lines = lines[start_index:end_index]
        rendered_lines, truncated_by = _render_read_page(
            candidate_lines,
            start_line=args.offset,
        )

        if not rendered_lines and truncated_by == "bytes":
            line_size = len(candidate_lines[0].encode("utf-8"))
            quoted_path = shlex.quote(args.path)
            return (
                f"[Line {args.offset} is {_format_size(line_size)}, exceeds "
                f"{_format_size(READ_MAX_BYTES)} limit. Use bash: sed -n "
                f"'{args.offset}p' {quoted_path} | head -c {READ_MAX_BYTES}]"
            )

        output = "\n".join(rendered_lines)
        next_offset = args.offset + len(rendered_lines)

        if truncated_by is not None:
            end_line = next_offset - 1
            byte_note = (
                f" ({_format_size(READ_MAX_BYTES)} limit)"
                if truncated_by == "bytes"
                else ""
            )
            notice = (
                f"[Showing lines {args.offset}-{end_line} of {total_lines}"
                f"{byte_note}. Use offset={next_offset} to continue.]"
            )
            return f"{output}\n\n{notice}"

        if end_index < total_lines:
            remaining = total_lines - end_index
            line_word = "line" if remaining == 1 else "lines"
            notice = (
                f"[{remaining} more {line_word} in file. "
                f"Use offset={end_index + 1} to continue.]"
            )
            return f"{output}\n\n{notice}"

        return output


class WriteArguments(BaseModel):
    path: str
    content: str


class WriteTool:
    type = "function"
    name = "write"
    description = "Write content to file"

    parameters: ClassVar[dict[str, Any]] = WriteArguments.model_json_schema()
    arguments_model = WriteArguments

    async def run(self, arguments: dict[str, Any]) -> str:
        args = self.arguments_model.model_validate(arguments)
        await asyncio.to_thread(Path(args.path).write_text, args.content)
        return "ok"


class EditArguments(BaseModel):
    path: str
    old: str
    new: str
    all: bool = False


class EditTool:
    type = "function"
    name = "edit"
    description = "Replace old with new in file (old must be unique unless all=true)"
    parameters: ClassVar[dict[str, Any]] = EditArguments.model_json_schema()
    arguments_model = EditArguments

    async def run(self, arguments: dict[str, Any]) -> str:
        args = self.arguments_model.model_validate(arguments)
        path = Path(args.path)
        text = await asyncio.to_thread(path.read_text)
        if args.old not in text:
            return "error: old_string not found"
        count = text.count(args.old)
        if not args.all and count > 1:
            return (
                f"error: old_string appears {count} times, "
                "must be unique (use all=true)"
            )
        replacement = (
            text.replace(args.old, args.new)
            if args.all
            else text.replace(args.old, args.new, 1)
        )
        await asyncio.to_thread(path.write_text, replacement)
        return "ok"


class GlobArguments(BaseModel):
    pat: str
    path: str = "."


class GlobTool:
    type = "function"
    name = "glob"
    description = "Find files by pattern, sorted by mtime"
    parameters: ClassVar[dict[str, Any]] = GlobArguments.model_json_schema()
    arguments_model = GlobArguments

    async def run(self, arguments: dict[str, Any]) -> str:
        args = self.arguments_model.model_validate(arguments)
        pattern = (args.path + "/" + args.pat).replace("//", "/")
        files = globlib.glob(pattern, recursive=True)
        files = sorted(
            files,
            key=lambda path: os.path.getmtime(path) if os.path.isfile(path) else 0,
            reverse=True,
        )
        return "\n".join(files) or "none"


class GrepArguments(BaseModel):
    pat: str
    path: str = "."


class GrepTool:
    type = "function"
    name = "grep"
    description = "Search files for regex pattern"
    parameters: ClassVar[dict[str, Any]] = GrepArguments.model_json_schema()
    arguments_model = GrepArguments

    async def run(self, arguments: dict[str, Any]) -> str:
        args = self.arguments_model.model_validate(arguments)
        pattern = re.compile(args.pat)
        hits = await asyncio.to_thread(_grep_files, args.path, pattern)
        return "\n".join(hits[:50]) or "none"


def _grep_files(path: str, pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for filepath in globlib.glob(path + "/**", recursive=True):
        try:
            lines = Path(filepath).read_text().splitlines()
        except (OSError, UnicodeError):
            continue
        for line_number, line in enumerate(lines, 1):
            if pattern.search(line):
                hits.append(f"{filepath}:{line_number}:{line}")
    return hits


class BashArguments(BaseModel):
    cmd: str
    run_in_background : bool = False


class BashTool:
    type = "function"
    name = "bash"
    description = "Run shell command. If you set run_in_background to True, " \
    "the result will be collected on a later turn."
    parameters: ClassVar[dict[str, Any]] = BashArguments.model_json_schema()
    arguments_model = BashArguments

    bgManager : BackgroundManager

    def __init__(self,bgManager : BackgroundManager | None = None):
        self.bgManager = bgManager
        pass

    async def run(self, arguments: dict[str, Any]) -> str:
        args = self.arguments_model.model_validate(arguments)
        if args.run_in_background == True:
            taskID = self.bgManager.start(args.cmd)
            return (f"[Background task {taskID} started] "
                    "The result will be collected on a later turn.")
        process = await asyncio.create_subprocess_shell(
            args.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output_lines: list[str] = []

        async def collect_output() -> None:
            assert process.stdout is not None
            while line := await process.stdout.readline():
                text = line.decode(errors="replace")
                print(f"  {_DIM}│ {text.rstrip()}{_RESET}", flush=True)
                output_lines.append(text)
            await process.wait()

        try:
            await asyncio.wait_for(collect_output(), timeout=30)
        except TimeoutError:
            process.kill()
            await process.wait()
            output_lines.append("\n(timed out after 30s)")
        return "".join(output_lines).strip() or "(empty)"


class LoadSkillsArguments(BaseModel):
    skill_name: str


class LoadSkillsTool:
    type = "function"
    name = "load_skills"
    description = "load an exist skill."
    parameters: ClassVar[dict[str, Any]] = LoadSkillsArguments.model_json_schema()
    arguments_model = LoadSkillsArguments

    def __init__(self, skill_registry: SkillRegistry):
        self.skill_registry = skill_registry

    async def run(self, arguments: dict[str, Any]) -> str:
        args = self.arguments_model.model_validate(arguments)
        content = self.skill_registry.get_content(args.skill_name)
        if content is None:
            return f"error: skill name: {args.skill_name} not found!"
        return content
