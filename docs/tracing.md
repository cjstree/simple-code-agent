# 追踪与遥测

Phoenix 和本地 JSONL 追踪均为可选功能。安装遥测依赖：

```bash
uv sync --extra telemetry
```

如需导出到 Phoenix，在 `.env` 中启用 `PHOENIX_ENABLED` 并配置其端点。如需将 span
写入本地，可以单独设置 `TRACE_LOG_DIR`，也可以与 Phoenix 同时启用：

```dotenv
TRACE_LOG_DIR=artifacts/traces
```

完成的 span 以每行一个紧凑 JSON 对象的形式追加到 `trace_log_<session-id>.jsonl`。
每行包含 OpenTelemetry 的 trace/span ID、时间戳、状态、属性、事件和资源字段。同一目录
中的 `manifest.jsonl` 记录 session ID 与 trace 文件的对应关系。

多轮 session 共用一个文件，但每轮有独立的 trace ID。写入是同步的，因此文件中的行按
span 完成时间排序，而非开始时间；还原执行顺序时应使用时间戳和父 span ID。

排队的 `memory.extract` span 使用提交该提取任务的轮次的 trace 上下文，即使处理在该轮
结束后才完成。Agent 关闭时排队的最后一次提取属于 session，没有轮次父节点。

每个完成的 `agent.turn` span 都记录 `agent.stop_reason`、`agent.tool_rounds`、
`agent.max_tool_rounds` 和 `llm.finish_reason`。如果 Agent 因达到工具调用轮数上限而停止，
`agent.stop_reason` 为 `max_tool_rounds`，而 `llm.finish_reason` 保留模型服务商返回的最终值，
通常为 `tool_calls`。

Inspect 文件修改评测中的 trace 落盘位置和评分读取方式见[文件修改评测指南](file-edit-evaluation.md#模型与追踪配置)。
