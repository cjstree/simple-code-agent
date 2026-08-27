@dataclass  
interface EntryBase {
    id: string
    seq: number
    parentId: string | null
    timestamp: number
  }
  interface CompactionEntry {
    type: "compaction"
    summary: string
    retainedTail: AgentMessage[]
    tokensBefore: number
  }
  interface SessionStats {
    messageCount: number
    cachedTokens: number
    uncachedTokens: number
    totalTokens: number
    costTotal: number
  }


  工具执行结果是另一条 event：
 
  {
      "type": "message",
      "seq": 11,
      "message": {
          "role": "tool",
          "tool_call_id": "call-1",
          "content": "..."
      }
  }
 
  如果需要支持崩溃恢复，还可以保存执行状态：
 
  tool_started
  tool_finished
 
  它们不进入模型上下文，只用于判断工具是否执行完成。

class Session:
    events: list[dict]
    next_seq: int
    message_count: int
    lifetime_tokens: int

    def append_message(self, message, estimated_tokens):
        self.events.append({
            "type": "message",
            "seq": self.next_seq,
            "id": new_id(),
            "message": message,
            "estimated_tokens": estimated_tokens,
        })
        self.next_seq += 1
        self.message_count += 1

    def append_compaction(self, summary, retained_tail, tokens_before):
        self.events.append({
            "type": "compaction",
            "seq": self.next_seq,
            "id": new_id(),
            "summary": summary,
            "retained_tail": retained_tail,
            "tokens_before": tokens_before,
        })
        self.next_seq += 1

     def build_active_context(events):
        last_compaction_index = next(
            (
                i for i in range(len(events) - 1, -1, -1)
                if events[i]["type"] == "compaction"
            ),
            None,
        )
    
        if last_compaction_index is None:
            return [
                event["message"]
                for event in events
                if event["type"] == "message"
            ]

        checkpoint = events[last_compaction_index]

        messages = [{
            "role": "user",  # 或自定义内部 role，发送前再转换
            "content": f"<context-summary>\n{checkpoint['summary']}\n</context-summary>",
        }]
        messages.extend(checkpoint["retained_tail"])

        messages.extend(
            event["message"]
            for event in events[last_compaction_index + 1:]
            if event["type"] == "message"
        )
         return messages

 典型调用流程是：
 
  session.append(user_message)
 
  while True:
      context = session.build_context()
      assistant = await llm(context)
      session.append(assistant)
 
      if not assistant.has_tool_calls:
          break
 
      for tool_call in assistant.tool_calls:
          result = await execute(tool_call)
          session.append(result)


大致是：
 
  判断需要 compact
      ↓
  选择保留的近期消息
      ↓
  选择需要总结的旧消息
      ↓
  序列化成总结 prompt
      ↓
  调用当前 LLM 生成结构化 summary
      ↓
  追加 compaction entry
      ↓
  重新派生模型上下文
 
  不是总结全部消息。prepareCompaction() 会：
 
  - 从后向前保留约 keepRecentTokens。
  - 找到 firstKeptEntryId。
  - 只总结更早的消息。
  - 多次 compact 时，将上一次 summary 作为输入增量更新。
  - 如果一个 turn 自身过大，分别总结历史和该 turn 的前半部分。
  - 额外保留读过、修改过的文件列表。
 
  准备逻辑在 packages/coding-agent/src/core/compaction/compaction.ts:736，LLM 总结调用在 packages/coding-agent/src/core/compaction/compaction.ts:844。

 生成的 summary 会被转换成 user message，发给后续 LLM：
 
  The conversation history before this point was compacted into the following summary:
 
  <summary>
  ...
  </summary>

### Compact 触发时机
 
  有三种：
 
  - 用户手动执行 /compact。
  - 一次 assistant response 结束后，发现超过阈值。
  - 发送下一个 user prompt 前再次检查，覆盖 aborted/error 等情况。
  - Provider 报 context overflow 时，compact 后自动重试一次。
 
  阈值为：
 
  contextTokens > contextWindow - reserveTokens