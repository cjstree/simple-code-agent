# Eval Trace 输入契约

本文描述 trace 自动评分阶段可依赖的 eval 端接口。它只定义 trace 输入和 turn 绑定，
具体 preserve oracle 与计分规则由后续阶段实现。

## 生成位置与生命周期

eval runner 始终把本地 trace 写入 sample sandbox 的 `.eval_traces/`：

- `.eval_traces/manifest.jsonl`：每个 session 一条索引；
- `.eval_traces/trace_log_<session-id>.jsonl`：该 session 的 completed spans。

scorer 必须在 Agent 子进程结束后、sandbox 清理前调用
`code_agent_evals.trace.read_trace_input(environment)`。`context_survival` 等 scorer-only
metadata 不进入 runner payload，也不会暴露给 Agent。

## Python 接口

`read_trace_input` 返回 `EvaluationTrace`。也可以用纯函数
`parse_trace_jsonl(manifest_jsonl, trace_files)` 解析已读取的文本，便于测试和离线重放。

核心视图如下：

- `EvaluationTrace.sessions` / `.session(session_id)`：按 exporter session 查询；
- `EvaluationTrace.turns` / `.turn(ordinal)`：跨 Agent restart、按时间排序的一基 eval
  turn；
- `EvaluationTrace.compact_history_between(introduced_turn, used_turn)`：跨 session 的
  全局引入/使用窗口；
- `SessionTrace.turns` / `.turn(ordinal)`：按 `start_time` 排序的一基 turn；
- `SessionTrace.compact_history_between(introduced_turn, used_turn)`：信息引入后、使用
  turn 结束前、同一 session 内发生的 history compact；
- `TurnTrace.compact_spans`、`.compact_history_spans`、`.tool_spans`：已绑定到该 turn
  的评分信号；
- `TurnTrace.spans_named(name)`：供 memory 等后续评分信号使用；
- `TraceSpan.output_value`：summary 或 tool result 的未脱敏输出。

`descendants` 包含 agent turn 的全部后代，而不只直接子 span。因此评分器不需要依赖
OpenAI instrumentation 是否在操作 span 中间插入了额外层级。

## 排序与绑定规则

JSONL 由 completed span exporter 写出，子 span 通常先于父 span落盘；行顺序不得表示
执行顺序。解析器先读取完整 session，再按 `(start_time, end_time, span_id)` 稳定排序，
并使用同一 `trace_id` 下的 `parent_id` 祖先链把 span 绑定到 `agent.turn`。

以下情况抛出 `TraceInputError`，调用方应归类为评测基础设施失败，而不是普通零分：

- manifest 或引用文件缺失（`TraceUnavailableError`）；
- JSON、schema version、时间戳、session 对应关系损坏（`TraceFormatError`）；
- manifest 文件路径逃逸、session/file 重复或 span ID 重复；
- `session.compact`、`session.compact_history` 或 tool span 无法绑定到 agent turn。

没有触发 compact 不是输入损坏：此时对应集合为空，由后续 scorer 记
`activated=false`。

## 后续评分约定

后续 `context_survival` scorer 应基于上述视图，为每个检查生成：

- `activated`：`compact_history_between(...)` 数量达到 `min_compact_hops`；
- `trace_preserved`：指定 compact summary 的 `output_value` 满足稳定标记断言；
- `behavior_passed`：声明的 pytest selector 全部通过；
- `strict_pass = activated and trace_preserved and behavior_passed`。

如果 sample 未声明 `context_survival`，继续走当前 pytest scorer，不读取 trace，因此
现有 file-edit eval 的评分行为保持不变。
