"""Agent orchestration."""

import json
import os
import re
import uuid
from contextlib import AsyncExitStack
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openai import OpenAI

from code_agent.mcp_tool import MCPTool
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry
from code_agent.tool import (
    BashTool,
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    TodoWriteTool,
    WriteTool,
)
from code_agent.tool_registry import ToolRegistry
from code_agent.context_manager import ContextManager

OPENROUTER_KEY = None
MODEL = settings.llm_model_name

# ANSI colors
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, YELLOW, RED = (
    "\033[34m",
    "\033[36m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
)


def separator():
    width = os.get_terminal_size().columns if os.isatty(1) else 80
    return f"{DIM}{'─' * min(width, 80)}{RESET}"


def render_markdown(text):
    return re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)


def format_tool_arguments(arguments: dict[str, Any]) -> str:
    """Format every argument so approval decisions have useful context."""
    return json.dumps(arguments, ensure_ascii=False, indent=2, default=str)


def rsp2msg(resp):
    return {
        "role": "assistant",
        "content": resp.content,
        **(
            {
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in resp.tool_calls
                ]
            }
            if resp.tool_calls
            else {}
        ),
    }


class Agent:
    "an agent implemention. using cli command to intereact. can use tools."

    client: OpenAI
    tool_registry: ToolRegistry
    messages: list
    system_prompt: str
    max_tool_round: int
    hooks: dict[str, list[callable]]
    context_manager : ContextManager

    def __init__(self, telemetry: AgentTelemetry | None = None):
        self.telemetry = telemetry or AgentTelemetry()
        self._owns_telemetry = telemetry is None
        self.session_id = str(uuid.uuid4())
        self.hooks = {
            "UsrPromptSubmit": [],
            "PreToolUse": [],
            "PostToolUse": [],
            "Stop": [],
        }

    def registHook(self, event: str, func: callable):
        self.hooks[event].append(func)

    def triggerHook(self, event: str, **kargs):
        for func in self.hooks[event]:
            func(kargs)

    async def start(self):
        if self._owns_telemetry:
            self.telemetry = AgentTelemetry.initialize(
                enabled=settings.phoenix_enabled,
                endpoint=settings.phoenix_collector_endpoint,
                project_name=settings.phoenix_project_name,
            )
        try:
            if not settings.llm_api_key or not settings.llm_model_name:
                raise RuntimeError(
                    "LLM_API_KEY and LLM_MODEL_NAME must be configured in agent/.env"
                )
            self.client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
            self.context_manager = ContextManager(self.client)
            tool_list = [
                ReadTool(),
                BashTool(),
                EditTool(),
                GlobTool(),
                GrepTool(),
                WriteTool(),
                TodoWriteTool(),
            ]
            async with AsyncExitStack() as stack:
                if settings.mcp_url:
                    read_stream, write_stream = await stack.enter_async_context(
                        streamable_http_client(settings.mcp_url)
                    )
                    session = await stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    server_info = await session.initialize()
                    print(f"Connected MCP server: {server_info.server_info}")
                    mcp_tools = await MCPTool.discover(session=session)
                    tool_list.extend(mcp_tools)

                self.tool_registry = ToolRegistry(
                    tools=tool_list,
                    sensitive_tools={"bash", "edit", "write"},
                )
                self.system_prompt = f"Concise coding assistant. cwd: {os.getcwd()}"
                self.max_tool_round = 5
                await self._loop()
        finally:
            if self._owns_telemetry:
                self.telemetry.shutdown()

    async def _loop(self):
        print(
            f"{BOLD}nanocode{RESET} | {DIM}{MODEL} ({'OpenRouter' if OPENROUTER_KEY else 'OpenAI'}) | {os.getcwd()}{RESET}\n"
        )
        self.messages = []
        try:
            while True:
                print(separator())
                user_input = input(f"{BOLD}{BLUE}❯{RESET} ").strip()
                print(separator())

                if not user_input:
                    continue
                if user_input in ("/q", "exit"):
                    break
                if user_input == "/c":
                    self.messages = []
                    self.session_id = str(uuid.uuid4())
                    print(f"{GREEN}⏺ Cleared conversation{RESET}")
                    continue

                # compact message
                self.messages = self.context_manager.compact(self.messages)
                # start a new span
                with self.telemetry.trace_turn(
                    session_id=self.session_id, prompt=user_input
                ) as turn_span:
                    self.messages.append({"role": "user", "content": user_input})
                    await self._tool_call_loop()
                    if self.messages[-1]["role"] == "assistant":
                        turn_span.set_output(self.messages[-1]["content"])
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}Goodbye!{RESET}")

    async def _tool_call_loop(self):
        cur_round = 0
        while cur_round < self.max_tool_round:
            # compact tool res before send to llm
            self.messages =  self.context_manager._tool_res_compact(self.messages)
            # call the llm.
            resp = self._call_api(self.messages, self.system_prompt)
            self.messages.append(rsp2msg(resp))
            # print response content
            if resp.content:
                print(f"\n{CYAN}⏺{RESET} {render_markdown(resp.content)}")
            # if contain tool_call, call the tools
            if not resp.tool_calls:
                return
            for tool_call in resp.tool_calls:
                tool_name = tool_call.function.name
                try:
                    with self.telemetry.trace_tool(
                        tool_call_id=tool_call.id,
                        tool_name=tool_name,
                        arguments=tool_call.function.arguments,
                    ) as tool_span:
                        tool_args = json.loads(tool_call.function.arguments)
                        argument_text = format_tool_arguments(tool_args)
                        print(
                            f"\n{GREEN}⏺ {tool_name.capitalize()}{RESET}"
                            f"({DIM}{argument_text}{RESET})"
                        )
                        # check permission
                        approved = True
                        if self.tool_registry.requires_approval(tool_name):
                            approved = self._ask_permission(tool_name)
                        # run the tool
                        res = await self.tool_registry.run_tool(
                            tool_name=tool_name,
                            arguments=tool_args,
                            approved=approved,
                        )
                        tool_span.set_output(res)
                except json.JSONDecodeError as err:
                    res = f"error: invalid JSON tool arguments: {err}"
                # Tools can raise provider-, validation-, or operating-system errors.
                except Exception as err:  # noqa: BLE001
                    res = f"error: tool run error:{err}"

                # result preview:
                self._res_preview(res)
                # store the result
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": res,
                    }
                )
            cur_round += 1
            print()
        if cur_round == self.max_tool_round:
            print("Maximum tool-call rounds reached.")

    def _ask_permission(self, tool_name: str) -> bool:
        decision = input(f"{YELLOW}Approve {tool_name} for this call? [y/N] {RESET}")
        return decision.strip().lower() in {
            "y",
            "yes",
        }

    def _res_preview(self, res: str) -> None:
        result_lines = res.split("\n")
        preview = result_lines[0][:60]
        if len(result_lines) > 1:
            preview += f" ... +{len(result_lines) - 1} lines"
        elif len(result_lines[0]) > 60:
            preview += "..."
        print(f"  {DIM}⎿  {preview}{RESET}")

    def _call_api(self, messages: list, system_prompt: str):
        openai_messages = [{"role": "system", "content": system_prompt}, *messages]
        completion = self.client.chat.completions.create(
            model=settings.llm_model_name,
            messages=openai_messages,
            tools=self.tool_registry.get_tools_desc(),
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
        return completion.choices[0].message
