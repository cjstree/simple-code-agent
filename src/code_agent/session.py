import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from openai.types import CompletionUsage
from code_agent.telemetry import AgentTelemetry
from code_agent.settings import settings


SUMMARIZER_SYS_PROMPT = (
    "You are a technical Summarizer for Coding Sessionst. Your task is to summarize the conversation"
    "between the user and the coding agent so that the same agent later"
    "can seamlessly resume work.\n\n"
    "Your summary must be concise, factual, and actionable. Organize it under these exact "
    "sections (use markdown bullet points or numbered lists):\n"
    "1. **Current Goal** – What is the primary objective right now?\n"
    "2. **Key Findings & Decisions** – Important insights, conclusions, or choices made during "
    "the conversation (bullet list).\n"
    "3. **Files Read or Modified** – List all files that were examined, edited, or created, "
    "with a brief note on each (e.g., 'config.py – updated timeout value').\n"
    "4. **Remaining Work** – What still needs to be done? List unresolved tasks or next steps.\n"
    "5. **User Constraints** – Any explicit restrictions, preferences, or requirements the user "
    "has stated (e.g., 'must use Python 3.9', 'avoid external APIs').\n\n"
    "Avoid repetition, filler, or speculation. Prioritize information that directly affects "
    "continuation of the work. If a section has no content, state 'None'.\n"
)


@dataclass
class Entry:
    type: str
    role: str
    seq: int
    # Token usage of this entry and all context before it.
    token_before: int
    # Token usage attributed to this entry.
    token_estimate: int


@dataclass
class MessageEntry(Entry):
    type: str = field(init=False, default="message")
    # Raw message content.
    content: str  
    # Used instead of content when constructing a compacted context.
    replace_content: Any | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


@dataclass
class CompactEntry(Entry):
    type: str = field(init=False, default="compact")
    # context token usage before compact.
    token_before_compact : int
    # summary of old entry.
    summary : str


# Estimate token usage of a single message.
def estimate(message: dict[str, Any]) -> int:
    serialized = json.dumps(message, ensure_ascii=False, default=str)
    return max(1, (len(serialized) + 3) // 4)


class Session:
    max_tool_res: int = 10
    max_tool_round_res: int = 200000
    persist_threshold: int = 20000
    persist_preview_head_chars: int = 500
    persist_preview_tail_chars: int = 1000

    entrys: list[Entry]
    next_seq: int
    # token count of [check_point:]
    context_token: int
    # seq of last history compact message.
    check_point : int
    # system prompt of the agent, must not be empty.
    system_prompt : str
    telemetry: AgentTelemetry
    client : AsyncOpenAI
    compact_thresh_hold: int
    reserved_token: int

    def __init__(
        self,
        sys_prompt: str,
        client: AsyncOpenAI,
        thresh_hold: int = 128_000,
        telemetry: AgentTelemetry | None = None,
        reserved_token: int = settings.llm_max_tokens,
    ):
        self.entrys = []
        self.next_seq = 0
        # an estimated number, total token usage in current created context.
        self.context_token = 0 
        self.check_point = 0
        self.system_prompt = sys_prompt
        self.client = client
        self.telemetry = telemetry if telemetry is not None else AgentTelemetry()
        self.compact_thresh_hold = thresh_hold
        self.reserved_token = reserved_token

    def append_message(
        self,
        message: dict[str, Any],
        usage: CompletionUsage | None = None,
    ) -> None:
        
        token_estimate = (
            usage.completion_tokens if usage is not None else estimate(message)
        )
        if usage is not None:
            # API usage measures the whole context, so use it to correct earlier
            # local estimates instead of accumulating overlapping totals.
            self.context_token = usage.total_tokens
        else:
            self.context_token += token_estimate

        content = message.get("content")
        entry = MessageEntry(
            role=message["role"],
            seq=self.next_seq,
            token_before=self.context_token,
            token_estimate=token_estimate,
            content=content,
            replace_content=None,
            tool_calls=message.get("tool_calls"),
            tool_call_id=message.get("tool_call_id"),
        )
        self.entrys.append(entry)
        self.next_seq += 1

    def build_context(self) -> list[dict[str,Any]]:
        """
        start from check_point. build message list from entry.
        """
        messages = [{"role":"system","content":self.system_prompt}]
        for entry in self.entrys[self.check_point:]:
            if entry.type == "compact":
                # use summary to replace old entry
                messages.append({"role":"user","content":entry.summary})
            elif entry.type == "message":
                # If have replace content, replace the original content with replace_content.
                message = {"role":entry.role,"content":entry.replace_content if entry.replace_content else entry.content}
                if entry.tool_calls:
                    message["tool_calls"] = entry.tool_calls
                if entry.tool_call_id:
                    message["tool_call_id"] = entry.tool_call_id
                messages.append(message)
        return messages

    async def compact(self) -> None:
        self._micro_compact()
        await self.compact_history()

    def update_sys_prompt(self,new_sys : str) :
        self.system_prompt = new_sys

    # may discard useful read-file result.
    # Prefer keeping the newest max_tool_res results. Older short results may stay
    # unchanged, so max_tool_res is not a hard cap on the number of tool messages.
    def _micro_compact(self) -> None:
        """
        Compact older, large tool results in the active window.
        may discard useful read-file result.
        Prefer keeping the newest max_tool_res results. Older short results may stay
        unchanged, so max_tool_res is not a hard cap on the number of tool messages
        """
        active_entries = self.entrys[self.check_point:]
        remaining_tool_results = sum(
            isinstance(entry, MessageEntry) and entry.role == "tool"
            for entry in active_entries
        )

        for entry in active_entries:
            if remaining_tool_results <= self.max_tool_res:
                break
            if not isinstance(entry, MessageEntry) or entry.role != "tool":
                continue

            content = (
                entry.replace_content
                if entry.replace_content is not None
                else entry.content
            )
            if len(content) > 250:
                entry.replace_content = (
                    "<tool-result-truncated>\n"
                    "Earlier tool result compacted. Re-run with refined "
                    "parameters if needed.\n"
                    "</tool-result-truncated>"
                )
            remaining_tool_results -= 1

    def tool_res_compact(self) -> None:
        """Persist oversized results from the latest active tool-call round."""
        active_entries = self.entrys[self.check_point:]
        round_start = len(active_entries)
        while round_start > 0:
            entry = active_entries[round_start - 1]
            if not isinstance(entry, MessageEntry) or entry.role != "tool":
                break
            round_start -= 1

        results_by_size = []
        for index, entry in enumerate(
            active_entries[round_start:], start=round_start
        ):
            content = (
                entry.replace_content
                if entry.replace_content is not None
                else entry.content
            )
            results_by_size.append((len(content), index, entry, content))
        results_by_size.sort(
            reverse=True,
            key=lambda item: (item[0], item[1]),
        )

        remaining = sum(size for size, _, _, _ in results_by_size)
        for size, _, entry, content in results_by_size:
            if remaining <= self.max_tool_round_res:
                break
            if size < self.persist_threshold:
                break

            compacted_content = self._persist_large_output(entry, content)
            entry.replace_content = compacted_content
            remaining -= size - len(compacted_content)

    def _persist_large_output(self, entry: MessageEntry, content: str) -> str:
        output_dir = Path("task_output/tool_results")
        output_dir.mkdir(parents=True, exist_ok=True)

        tool_call_id = entry.tool_call_id or "tool_result"
        safe_id = "".join(
            char if char.isalnum() or char in "-_." else "_"
            for char in tool_call_id
        ).strip(".")
        output_path = output_dir / f"{safe_id or 'tool_result'}.txt"
        output_path.write_text(content, encoding="utf-8")

        preview_limit = (
            self.persist_preview_head_chars + self.persist_preview_tail_chars
        )
        if len(content) > preview_limit:
            preview = (
                content[: self.persist_preview_head_chars]
                + "\n...\n"
                + content[-self.persist_preview_tail_chars :]
            )
        else:
            preview = content
        return (
            "<tool-result-persisted>\n"
            f"{preview}\n"
            f"Full output saved to: {output_path}\n"
            "</tool-result-persisted>"
        )

    def need_compact(self) -> bool :
        return self.context_token + self.reserved_token >= self.compact_thresh_hold

    # TODO: avoid split tool_call and tool_result
    async def compact_history(self):
        if not self.need_compact():
            return

        # Token count of the active context that will be replaced by the summary.
        token_before_compact = self.context_token

        # Keep the system prompt in the source context so it also participates in
        # summarization.
        context = self.build_context()
        context_json = json.dumps(context,ensure_ascii=False)
        openai_message = [{"role":"system","content":SUMMARIZER_SYS_PROMPT},
                          {"role": "user", "content": f"Please summarize the history :{context_json}"}]
        completion = await self.client.chat.completions.create(
            model=settings.llm_model_name,
            messages=openai_message,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
        summary = completion.choices[0].message.content

        # Actual tokens generated for the summary text.
        summary_token = completion.usage.completion_tokens
        # After compaction, build_context contains the original system prompt and
        # the summary entry. The system message is estimated locally because its
        # tokens are not part of completion_tokens.
        compacted_context_token = summary_token + estimate(
            {"role": "system", "content": self.system_prompt}
        )

        entry = CompactEntry(
            role="user",
            seq=self.next_seq,
            token_before=compacted_context_token,
            token_estimate=summary_token,
            token_before_compact=token_before_compact,
            summary=summary
        )
        self.entrys.append(entry)
        self.check_point = entry.seq
        self.next_seq += 1
        self.context_token = compacted_context_token
