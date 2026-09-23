"""Agent orchestration."""

import inspect
import json
import os
import re
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aioconsole import ainput
from openai import AsyncOpenAI
from openai.lib.streaming.chat import ChatCompletionStreamState
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion

from code_agent.background_manager import BackgroundManager
from code_agent.context_manager import ContextManager
from code_agent.mcp_client import MCPClient
from code_agent.mcp_tool import MCPTool
from code_agent.memory import Memory
from code_agent.session import Session
from code_agent.settings import settings
from code_agent.skill_registry import SkillRegistry
from code_agent.telemetry import AgentTelemetry
from code_agent.tool import (
    BashTool,
    EditTool,
    GlobTool,
    GrepTool,
    LoadSkillsTool,
    ReadTool,
    TodoWriteTool,
    Tool,
    WriteTool,
)
from code_agent.tool_registry import ToolRegistry

OPENROUTER_KEY = None
MODEL = settings.llm_model_name
SKILLS_DIR = None
MEMORY_EXTRACTION_CLOSE_TIMEOUT = 5

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


@dataclass(frozen=True)
class AgentLoopResult:
    completion: ChatCompletion
    stop_reason: str
    tool_rounds: int


class Agent:
    "an agent implemention. using cli command to intereact. can use tools."

    client: AsyncOpenAI
    tool_registry: ToolRegistry
    system_prompt: str
    max_tool_round: int
    hooks: dict[str, list[callable]]
    context_manager : ContextManager
    session: Session
    skill_registry: SkillRegistry
    bgManager: BackgroundManager
    memory: Memory | None

    def __init__(
        self,
        telemetry: AgentTelemetry,
        system_prompt: str | None = None,
        memory_path: Path = Path("./memory"),
    ):
        self.telemetry = telemetry
        self.session_id = str(uuid.uuid4())
        self.hooks = {
            "UsrPromptSubmit": [], # after userinput, before llm submit.
            "PreLLMSubmit":[], # right before llm submit.
            "PreToolUse": [], # Before every tool use.
            "PostToolUse": [], # After tool use round.
            "Stop": [], # a turn stop. Before next turn begin.
        }
        self.bgManager = BackgroundManager()
        self.skill_registry = SkillRegistry()
        self.system_prompt = system_prompt
        self.session = Session(
            sys_prompt=system_prompt,
            client=None,  # type: ignore[arg-type]
            telemetry=telemetry,
        )
        self._exit_stack: AsyncExitStack | None = None
        self.mcp_client: MCPClient | None = None
        self.memory = None
        self.memory_path = memory_path
        self._closed = False

    def append_message(
        self,
        message: dict[str, Any],
        usage: CompletionUsage | None = None,
    ) -> None:
        self.session.append_message(message, usage=usage)

    def registHook(self, event: str, func: callable):
        self.hooks[event].append(func)

    async def triggerHook(self, event: str, **kargs):
        for func in self.hooks[event]:
            ret = func(**kargs)
            if inspect.isawaitable(ret):
                ret = await ret
            # if has return, means something wrong. Like permission deny
            if ret is not None:
                return ret


    def _create_local_tools(self) -> list[Tool]:
        return [
            ReadTool(),
            BashTool(self.bgManager),
            EditTool(),
            GlobTool(),
            GrepTool(),
            WriteTool(),
            TodoWriteTool(),
            LoadSkillsTool(self.skill_registry),
        ]

    async def compact(self) -> None :
        active_entry_count = len(self.session.entrys) - self.session.check_point
        if active_entry_count > 3:
            if self.session.need_compact():
                self.create_extract_task()
            await self.session.compact()

    def collect_bg_result(self) -> None:
        if self.bgManager:
            res = self.bgManager.collect()
            if res:
                lines = [
                    f"taskid:{id}  result:{result}"
                    for [id,result] in res
                ]
                header = f"collect {len(res)} background task result."
                content =  f"{header}\n" + "\n".join(lines)
                self.append_message({"role":"user","content":content})

    async def check_tool_permission(self,tool_name):
        if self.tool_registry.requires_approval(tool_name):
            approved = await self._ask_permission(tool_name)
            if approved == False:
                return False
    def compact_tool_res(self):
        self.session.tool_res_compact()

    def create_extract_task(self) -> None:
        if self.memory is None or not self.session.entrys:
            return
        self.memory.schedule_extract(self.session.build_context())

    async def start(self):
        if not settings.llm_api_key or not settings.llm_model_name:
            raise RuntimeError(
                "LLM_API_KEY and LLM_MODEL_NAME must be configured in agent/.env"
            )

        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        self.skill_registry.scan(Path("./skills"))
        self.context_manager = ContextManager(self.client, telemetry=self.telemetry)
        self.session = Session(sys_prompt=self.system_prompt,client=self.client,telemetry=self.telemetry)
        self.memory = Memory(
            path=self.memory_path,
            client=self.client,
            telemetry=self.telemetry,
        )
        tool_list = self._create_local_tools()
        # regist hooks

        self.registHook(event="UsrPromptSubmit",func = self.build_system)
        self.registHook(event="UsrPromptSubmit",func = self.inject_memory)
        self.registHook(event="UsrPromptSubmit",func = self.compact)
        self.registHook(event="PreLLMSubmit",func = self.collect_bg_result)
        self.registHook(event="PreToolUse",func = self.check_tool_permission)
        self.registHook(event="PostToolUse",func = self.compact_tool_res)

        exit_stack = AsyncExitStack()
        try:
            mcp_client = await exit_stack.enter_async_context(
                MCPClient(
                    url=settings.mcp_url,
                    connect_timeout=settings.mcp_connect_timeout,
                )
            )
            tool_list.extend(await MCPTool.discover(mcp_client))

            self.tool_registry = ToolRegistry(
                tools=tool_list,
                sensitive_tools={"bash", "edit", "write"},
            )
            self.max_tool_round = 5
        except BaseException:
            await exit_stack.aclose()
            raise

        self._exit_stack = exit_stack
        self.mcp_client = mcp_client

    async def close(self) -> None:
        try:
            if self._closed:
                return
            self.create_extract_task()
            self._closed = True
            if self.memory is not None:
                completed = await self.memory.close(
                    timeout=MEMORY_EXTRACTION_CLOSE_TIMEOUT
                )
                if not completed:
                    print(
                        "Memory extraction timed out after "
                        f"{MEMORY_EXTRACTION_CLOSE_TIMEOUT} seconds."
                    )
        finally:
            self._closed = True
            exit_stack = self._exit_stack
            self._exit_stack = None
            self.mcp_client = None
            if exit_stack is not None:
                await exit_stack.aclose()

    # basic io loop, read msg from user input
    async def cli_loop(self):
        print(
            f"{BOLD}nanocode{RESET} | {DIM}{MODEL} ({'OpenRouter' if OPENROUTER_KEY else 'OpenAI'}) | {os.getcwd()}{RESET}\n"
        )
        try:
            while True:
                print(separator())
                user_input = (await ainput(f"{BOLD}{BLUE}❯{RESET} ")).strip()
                print(separator())

                if not user_input:
                    continue
                if user_input in ("/q", "exit"):
                    break
                if user_input == "/c":
                    self.session = Session(
                        sys_prompt=self.session.system_prompt,
                        client=self.session.client,
                        thresh_hold=self.session.compact_thresh_hold,
                        telemetry=self.session.telemetry,
                        reserved_token=self.session.reserved_token,
                    )
                    self.session_id = str(uuid.uuid4())
                    print(f"{GREEN}⏺ Cleared conversation{RESET}")
                    continue

                # start a new span/turn
                # await self.run(input=user_input)
                with self.telemetry.trace_turn(
                    session_id=self.session_id, prompt=user_input
                ) as turn_span:
                    self.append_message({"role": "user", "content": user_input})
                    await self.triggerHook("UsrPromptSubmit")
                    loop_result = await self._agent_loop(stream=True)
                    self._record_turn_completion(turn_span, loop_result)

        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}Goodbye!{RESET}")

    # run a single input.
    async def run(
        self,
        input: str,
        *,
        stream: bool = False,
        new_session: bool = True,
    ) -> str:
        if new_session:
            self.session_id = str(uuid.uuid4())
        with self.telemetry.trace_turn(
            session_id=self.session_id, prompt=input
        ) as turn_span:
            self.append_message({"role": "user", "content": input})
            await self.triggerHook("UsrPromptSubmit")
            # TODO:simplify
            if stream:
                loop_result = await self._agent_loop(stream=True)
            else:
                loop_result = await self._agent_loop()
            self._record_turn_completion(turn_span, loop_result)
            return loop_result.completion.choices[0].message.content

    def _record_turn_completion(
        self, turn_span: Any, loop_result: AgentLoopResult | None
    ) -> None:
        if loop_result is None or not loop_result.completion.choices:
            return
        choice = loop_result.completion.choices[0]
        output = (
            choice.message.content
            if choice.message.content is not None
            else rsp2msg(choice.message)
        )
        turn_span.set_output(output)
        turn_span.set_attribute("agent.stop_reason", loop_result.stop_reason)
        turn_span.set_attribute("agent.tool_rounds", loop_result.tool_rounds)
        turn_span.set_attribute("agent.max_tool_rounds", self.max_tool_round)
        turn_span.set_attribute("llm.finish_reason", choice.finish_reason)

    # run agent loop.(Span)
    async def _agent_loop(self, *, stream: bool = False) -> AgentLoopResult:
        cur_round = 0
        while cur_round < self.max_tool_round:
            await self.triggerHook("PreLLMSubmit")
            messages = self.session.build_context()
            # call the llm.
            if stream:
                completion = await self._call_api(messages, stream=True)
            else:
                completion = await self._call_api(messages)
            choice = completion.choices[0]
            resp = choice.message
            self.append_message(rsp2msg(resp), usage=completion.usage)
            # print response content
            if resp.content and not stream:
                print(f"\n{CYAN}⏺{RESET} {render_markdown(resp.content)}")
            # if contain tool_call, call the tools
            if not resp.tool_calls:
                break
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

                        ret = await self.triggerHook("PreToolUse",tool_name = tool_name)
                        approved = ret is None

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
                self.append_message(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": res,
                    }
                )
                await self.triggerHook("PostToolUse")
            cur_round += 1
            # compact tool res before send to llm
            print()

        await self.triggerHook("Stop")
        # stop because achieve max tool call round
        if cur_round == self.max_tool_round:
            print("Maximum tool-call rounds reached.")
            stop_reason = "max_tool_rounds"
        else:
            # stop because not more tool calls
            stop_reason = completion.choices[0].finish_reason
        return AgentLoopResult(
            completion=completion,
            stop_reason=stop_reason,
            tool_rounds=cur_round,
        )

    async def _ask_permission(self, tool_name: str) -> bool:
        decision = await ainput(
            f"{YELLOW}Approve {tool_name} for this call? [y/N] {RESET}"
        )
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

    async def _call_api(
        self, messages: list, *, stream: bool = False
    ) -> ChatCompletion:
        openai_messages = messages
        request = {
            "model": settings.llm_model_name,
            "messages": openai_messages,
            "tools": self.tool_registry.get_tools_desc(),
            "max_tokens": settings.llm_max_tokens,
            "temperature": settings.llm_temperature,
        }
        if not stream:
            return await self.client.chat.completions.create(**request)

        started_output = False
        completion_stream = await self.client.chat.completions.create(
            **request,
            stream=True,
            stream_options={"include_usage": True},
        )
        stream_state = ChatCompletionStreamState()
        async with completion_stream:
            async for chunk in completion_stream:
                for event in stream_state.handle_chunk(chunk):
                    if event.type != "content.delta":
                        continue
                    if not started_output:
                        print(f"\n{CYAN}⏺{RESET} ", end="", flush=True)
                        started_output = True
                    print(event.delta, end="", flush=True)

        completion = stream_state.get_final_completion()

        if started_output:
            print()
        return completion

    # The memory injection should not change the system prompt.
    async def inject_memory(self) -> None:
        
        if self.memory:
            relevant = ""
            messages = self.session.build_context()
            relevant = await self.memory.select_relevant_memories(messages)
            if len(relevant) > 0:
                memory_records = "\n\n".join(
                    f"<memory>\n{memory}\n</memory>" for memory in relevant
                )
                msg = (
                    f"Potentially relevant memory:\n{memory_records}\n\n"
                    "The current user request and conversation context take priority over recalled memory."
                    )
                self.append_message({"role":"user","content":msg})

    async def build_system(self) -> None:
        # the first message must be system prompt!
        if self.system_prompt is not None:
            sys_prompt = self.system_prompt
        else:
            sections = [
                (
                    f"You are a coding agent at {os.getcwd()}. "
                    "You can use tools to solve tasks.\n"
                    f"Skills available:\n{self.skill_registry.format_list()}\n"
                    "Use load_skill to get full skill details if needed."
                ),
            ]
            sys_prompt = "\n\n".join(sections)

        self.session.update_sys_prompt(sys_prompt)
