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
