
import json

from openai import OpenAI
from pathlib import Path

from code_agent import settings


def is_tool_use_msg(msg:dict[str,str]) -> bool:
    if "tool_calls" in msg and msg["tool_calls"] != None:
        return True
    return False

def is_tool_call_res(msg:dict[str,str]) -> bool:
    return msg["role"] == "tool"

# find the boundary of a tool call round
# message structure:
# user_input -> resp(inclue tool use) -> tool_call_res -> tool_call_res -> resp(inclue tool use) -> tool_call_res -> resp(without tool_call)
def tool_call_range(messages:list[dict[str,str]],idx : int) :
    while is_tool_call_res(messages[idx]):
            idx -= 1
    tail = idx + 1
    while tail < len(messages) and is_tool_call_res(messages[tail]):
        tail += 1
    return idx,tail



class ContextManager:

    max_messages : int
    # max tool res size for single tool call
    max_tool_res : int 
    # max tool call res for a tool call round
    max_tool_round_res : int
    persist_threshold : int
    persist_preview_chars: int
    client : OpenAI
    context_limit : int

    def __init__(self,  client : OpenAI,persist_preview_chars: int | None = 1000):
        self.max_messages = 30
        self.max_tool_res = 5
        self.max_tool_round_res = 200000
        self.persist_threshold = 20000
        self.persist_preview_chars = persist_preview_chars
        self.client = client
        self.context_limit = 100000

    def compact(self,messages:list[dict[str,str]]):
        # result compact call after tool call round, not here
        ret = self._snip_compact(messages)
        ret = self._micro_compact(ret)

        cur_size = len(str(ret))
        if cur_size > self.context_limit :
            print(f"[auto compact]: current_size: {cur_size}  compact_threshold:{self.context_limit}")
            ret  = self._compact_history(ret)
            print(f"[auto compact]: finished. current_size: {len(str(ret))}")
        return ret


    # keep the message length not too long
    # Keep assistant tool_calls and all matching tool_call_id results atomic when snipping.
    def _snip_compact(self,messages:list[dict[str,str]]) -> list[dict[str,str]]:
        if len(messages) <= self.max_messages:
            return messages
        head_end, tail_start = 3, len(messages) - (self.max_messages - 3)


        while head_end < len(messages) and is_tool_call_res(messages[head_end]):
            head_end += 1

        while is_tool_call_res(messages[tail_start]):
            tail_start -= 1
        snipped = tail_start - head_end
        placeholder = {"role": "user", "content": f"[snipped {snipped} messages from conversation middle]"}
        return messages[:head_end] + [placeholder] + messages[tail_start:]

    # Prefer keeping the newest max_tool_res results. Older short results may stay
    # unchanged, so max_tool_res is not a hard cap on the number of tool messages.
    def _micro_compact(self,messages:list[dict[str,str]]) -> list[dict[str,str]]:
        cnt = 0
        for message in messages:
            if is_tool_call_res(message):
                cnt += 1;
        for message in messages:
            if cnt > self.max_tool_res:
                if is_tool_call_res(message):
                    if len(message['content']) > 120:
                        message['content'] = """<tool-result-truncated>
                        Earlier tool result compacted. Re-run if needed with refined parameters if needed.
                        </tool-result-truncated>"""
                    cnt -= 1
            else :
                break
        return messages
    
    # save large tool results in disk.
    # not the single tool call. If a tool call round contain too much content, compact the most large res until total
    # tool_res < max_tool_round_res. We use len(content) to estimate the token.
    def _tool_res_compact(self,messages:list[dict[str,str]]) -> list[dict[str,str]]:
        round_start = len(messages)
        while round_start > 0 and is_tool_call_res(messages[round_start - 1]):
            round_start -= 1
        # compact from the largest content
        results_by_size = sorted(
            (
                (len(messages[index]["content"]), index)
                for index in range(round_start, len(messages))
            ),
            reverse=True,
        )
        remaining = sum(size for size, _ in results_by_size)

        for size, index in results_by_size:
            if remaining <= self.max_tool_round_res:
                break
            if size < self.persist_threshold:
                break
            compacted_content = self._persist_large_output(messages[index])
            messages[index]["content"] = compacted_content
            remaining -= size - len(compacted_content)

        return messages

    # compact all history using llm, keep the sys prompt and the history summary
    def _compact_history(self,messages:list[dict[str,str]]) :
        summary = self._summary_history(messages)
        return [messages[0],{"role":"user","content":f"[Compacted]\n\n{summary}"}]

    # persist large_output in ./task_output/tool_results
    # return the content preview and file path of persist output
    def _persist_large_output(self,msg : dict[str,str]) -> str:
        tool_call_id = msg['tool_call_id']
        content = msg['content']

        output_dir = Path("task_output/tool_results")
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(
            char if char.isalnum() or char in "-_." else "_"
            for char in tool_call_id
        ).strip(".")
        output_path = output_dir / f"{safe_id or 'tool_result'}.txt"
        output_path.write_text(content, encoding="utf-8")

        preview = content[: self.persist_preview_chars]
        if len(content) > self.persist_preview_chars:
            preview += "..."
        return (
            "<tool-result-persisted>\n"
            f"{preview}\n"
            f"Full output saved to: {output_path}\n"
            "</tool-result-persisted>"
        )

    # summary the message history
    def _summary_history(self,messages : list[dict[str,str]]) -> str:
        history = json.dumps(messages,ensure_ascii=False)
        prompt = ("Summarize this coding-agent conversation so work can continue.\n"
              "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
              "4. remaining work, 5. user constraints.\nBe compact but concrete.\n\n" + history)
        openai_message = [{"role": "user", "content": prompt}]
        completion = self.client.chat.completions.create(
            model=settings.llm_model_name,
            messages=openai_message,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
        return completion.choices[0].message.content
