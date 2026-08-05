

def is_tool_use_msg(msg:dict[str,str]) -> bool:
    if "tool_calls" in msg and msg["tool_calls"] != None:
        return True
    return False

def is_tool_call_res(msg:dict[str,str]) -> bool:
    return msg["role"] == "tool"

class ContextManager:

    max_messages : int
    # max tool res size for single tool call
    max_tool_res : int 
    # max tool call res for a tool call round
    max_tool_round_res : int

    def __init__(self):
        self.max_messages = 30
        self.max_tool_res = 5
        self.max_tool_round_res = 200000

    # keep the message length not too long
    def _snip_compact(self,messages:list[dict[str,str]]) -> list[dict[str,str]]:
        if len(messages) <= self.max_messages:
            return messages
        head_end, tail_start = 3, len(messages) - (self.max_messages - 3)
        if head_end > 0 and is_tool_use_msg(messages[head_end - 1]):
            while head_end < len(messages) and is_tool_call_res(messages[head_end]):
                head_end += 1
        if (tail_start > 0 and tail_start < len(messages)
                and is_tool_call_res(messages[tail_start])
                and is_tool_call_res(messages[tail_start - 1])):
            tail_start -= 1
        snipped = tail_start - head_end
        placeholder = {"role": "user", "content": f"[snipped {snipped} messages from conversation middle]"}
        return messages[:head_end] + [placeholder] + messages[tail_start:]

    # remove some old tool use result.
    def _micro_compact(self,messages:list[dict[str,str]]) -> list[dict[str,str]]:
        cnt = 0
        for message in messages:
            if is_tool_call_res(message):
                cnt += 1;
        for message in messages:
            if cnt > self.max_tool_res:
                if is_tool_call_res(message):
                    if len(message['content'] > 120):
                        message['content'] = """<tool-result-truncated>
                        Earlier tool result compacted. Re-run if needed with refined parameters if needed.
                        </tool-result-truncated>"""
                cnt -= 1
            else :
                break
        return message
    
    # save large tool results in disk.
    def _tool_res_compact(self,messages:list[dict[str,str]]) -> list[dict[str,str]]:
        n = len(messages)
        idx = n
        content_bytes = 0
        while idx > 0 and is_tool_call_res(messages[idx-1]):
            idx -= 1
            content_bytes += len(messages[idx])
        # todo. Sort the large tool res. Compact from biggest res until cur_bytes <= max_bytes
