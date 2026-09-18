# Agent Context Survival Benchmark

## 目标

这个 benchmark 用于衡量 Agent 在 context compact 后能否保留并正确应用关键信息，
集中覆盖四类 preserve：

- constraint；
- decision update；
- entity binding；
- tool result。

整体设计从 `rollback_reason_long_horizon` 演进而来，但会先拆分原子能力，再通过较长的
mixed 样例观察组合场景。

## 评测模型

每个检查同时使用三类证据：

1. trace 证明实际经过了目标 compact 路径；
2. compact 结果保留了检查需要的信息；
3. visible 或 hidden tests 证明最终外部行为正确。

Agent 的最终文字报告不作为成功证据。trace 缺失或损坏属于评测基础设施问题；运行完成
但没有触发要求的 compact，则记为路径未激活。

## 阶段划分

工作拆为三个 spec：

1. [Trace 自动评分基础](01-trace-scoring-foundation.md)：让本地 trace 成为可靠、可机读的
   eval 输入。
2. [原子 preserve 样例](02-atomic-preservation-cases.md)：分别建设四类集中样例。
3. [对照、mixed 样例与报告](03-controls-mixed-and-reporting.md)：加入 compact-on/off 对照、
   组合样例和 benchmark 指标。

每个阶段都应保持现有 file-edit eval 可用。fixture 继续只作为不可变输入，每次运行仍在
独立 workspace 中完成。

## 初始规模

第一版每类建设两个原子样例，再增加一到两个 mixed 样例。原子样例应保持实现简单，使
失败主要能归因到 context survival，而不是代码任务本身的复杂度。

tool call/result 配对、持久化文件内容完整等底层 compact 不变量，使用确定性单元测试覆盖；
端到端 benchmark 主要考察语义存活和最终行为。

## 当前可运行样例

`evals/context_survival.jsonl` 目前包含一个 `constraint_rollback_compatibility` 原子样例。
它使用独立的 rollback fixture，不改变现有 file-edit 样例。第一轮声明 `C-11`（service
签名与必填参数）和 `C-12`（旧行可读、保存时保留其他字段），第二轮声明 `C-13`（重复
rollback 保留第一次的 reason，且只记录一次审计事件）。随后几轮调查 API、存储和测试，
并分析一份看似省事、却会违反这些约束的候选方案。之后继续检查状态转换、存储行映射、
请求形态和重复调用的结果；第十轮仅要求按前述要求实现。

旧 API 请求缺少 `reason` 时应在 API 边界补上 `unspecified`，而不是把 service 参数改成
可选；保存 reason 时应保留存储行的其他字段，不能直接重建整行；重复请求也不能覆盖首次
reason。每项 check 分别绑定能捕获对应错误的可见或隐藏 pytest selector。

评分器读取 `.eval_traces/`，要求每项约束从声明到第十轮之间至少经过两次
`session.compact_history`，且窗口内每次 summary 都包含对应标记；随后运行声明的行为
测试。标记只证明摘要提到了约束，语义是否正确由行为测试判断。每项检查记录
`activated`、`trace_preserved`、`behavior_passed`、`strict_pass` 和实际 compact 次数。
缺失或损坏的 trace 抛出基础设施错误。

样例设置 `compact_thresh_hold=4000`、`reserved_token=700`，即活跃上下文约达
3300 token 时触发历史 compact。此配置旨在减少之前几乎每轮一次的摘要；实际 compact
次数仍以运行 trace 为准，少于两次时该检查不会激活。

### 评分规则（版本 1）

每个 preserve check 的混合分取值为 0～1，由以下固定权重相加：

| 维度 | 权重 | 得分条件 |
| --- | ---: | --- |
| compact 激活 | 0.25 | 引入与使用 turn 之间达到 `min_compact_hops` |
| summary 留存 | 0.25 | 已激活，且窗口内**每次** `session.compact_history` 的 summary 都包含指定标记 |
| 行为测试 | 0.50 | 每个 check 内的 `behavior_tests` selector 单独运行、等权分配；当前 C-11/C-13 各有两项，C-12 有三项 |

`strict_pass` 仅在激活、留存和全部行为测试都通过时为 true，与混合分分开记录。
例如，某项 check 只有一次 compact 且行为测试全过，得 0.50、`strict_pass=false`；
两次 compact 和标记都满足，但该 check 的两项行为测试只过一项，得 0.75、
`strict_pass=false`。`Score.value` 是三项 check 混合分的算术平均；
`metadata.context_survival.checks` 保留每项的分数、维度证据、逐项测试结果和
`strict_pass`，顶层 `metadata.context_survival.strict_pass` 要求所有 check 严格通过。

运行结果中的分项分数可在 `uv run inspect view start` 打开的日志查看器中查看，
也可使用 `uv run inspect log dump <运行生成的 .eval 文件>` 查看 JSON 中的
`samples[].scores` 和 `metadata.context_survival`。评分解释还包含每项的简短分数摘要。

在仓库根目录运行：

```bash
uv run inspect eval src/code_agent_evals/tasks.py@context_survival_eval \
  --sample-id constraint_rollback_compatibility
```

此命令需要已配置可用的 Agent 模型。compact 激活取决于模型实际工具调用和上下文长度；
若未达到两次，结果会明确显示 `activated=false`，不会误记为通过。
该样例在 metadata 中设置 `runner_timeout_seconds=720`，为增加的调查回合留出执行时间；
其他样例继续使用 300 秒默认值。此字段仅供 solver 设置 runner 超时，不传给 Agent。
