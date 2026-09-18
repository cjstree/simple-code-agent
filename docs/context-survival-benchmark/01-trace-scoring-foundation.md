# Spec 1：Trace 自动评分基础

## 目标

让 compact trace 成为 context survival 样例必需且可自动评分的输入。

## 范围

- 将 benchmark trace 导出到 sample sandbox 内的固定目录。
- Agent 结束后、sandbox 清理前，读取 `manifest.jsonl` 和它引用的 session trace。
- 使用时间戳和 parent/span 关系重建 turn，不能把 JSONL 行顺序当作执行顺序。
- 将 `session.compact_history`、`session.compact` 和 tool span 关联到对应的
  `agent.turn`。
- 为超大 tool result 的持久化补充结构化 trace，至少记录是否 compact 和输出路径。
- 定义并校验 scorer 专用的 `context_survival` metadata；这些 oracle 信息不能传给 Agent。

## 评分契约

每个 preserve 检查至少记录：

- `activated`：信息引入与使用之间是否发生了要求的 compact；
- `trace_preserved`：稳定标记是否存在于指定 summary；
- `behavior_passed`：对应 pytest selector 是否通过；
- `strict_pass`：上述必要证据是否全部通过。

契约描述检查 ID、类别、信息引入和使用 turn、最少 compact hop、trace 断言和行为测试。

混合分采用 [README 的评分规则（版本 1）](README.md#评分规则版本-1)：compact 激活
占 0.25、summary 留存占 0.25、逐项行为测试占 0.50。`Score.value` 是各 check 的
平均混合分；`strict_pass` 独立记录，只有三类证据全部满足才为 true。

## 验收标准

- 合成 trace 测试覆盖正常、损坏、缺失、按完成顺序写入以及 compact 绑定错误的情况。
- trace 导出失败与普通 score-zero 结果可以区分。
- 没有 compact 激活或行为 oracle 通过时，样例不能获得 `strict_pass`。
- 未声明 `context_survival` 的现有样例继续使用当前 pytest 评分方式。
