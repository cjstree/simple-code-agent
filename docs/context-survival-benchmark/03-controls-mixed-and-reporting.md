# Spec 3：对照、Mixed 样例与报告

## 目标

将 compact 的影响与普通 long-horizon 难度分开测量，再观察多类 preserve 同时出现时的
表现。

## 配对对照

每个原子样例定义两个共享 turns、fixture 和行为测试的 dataset variant：

- `compact_on` 使用能够稳定触发目标 compact 的阈值；
- `compact_off` 使用足够高的阈值，保留未压缩历史。

多个 sample ID 共享 fixture 或 hidden tests 时，使用经过校验的 family/oracle ID，避免
复制预期行为。运行结果记录准确的 Agent 和 compact 配置，便于复现。

## Mixed 样例

原子样例稳定后增加一到两个长样例。每个 mixed case 至少包含四类 preserve 各一个检查，
并复用原子样例的逐项 trace 与行为契约。

`rollback_reason_long_horizon` 可以作为第一个 mixed case：标记已有 constraint，并补充或
拆出明确可评分的 decision update、entity binding 和 tool-result 信息。

## 报告指标

至少报告：

- compact 激活率；
- 各类别的 trace preservation 和行为成功率；
- strict end-to-end 通过率；
- compact-on 与 compact-off 的成功率差值；
- 按 compact hop 统计的 survival rate；
- mixed case strict pass rate。

模型边界无法提供确定性采样时，应运行多个 epoch。单次运行的 trace 证据保存在 score
metadata 中用于诊断，但不能作为提交到仓库的预期结果。

## 验收标准

- compact-on/off 除声明的 context 配置和 trace 路径预期外保持一致。
- 报告分别展示路径激活、语义保留和最终行为，而不是压缩成一个无法解释的分数。
- mixed case 失败可以追溯到具名的类别检查。
- 文档列出支持的 metadata 字段，以及运行单个样例和完整 benchmark 的命令。
