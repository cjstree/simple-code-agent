# Spec 2：原子 Preserve 样例

## 目标

建设可以独立测量四类 context survival 的集中样例。

## 样例集合

每类先建设两个样例：

- **Constraint preservation**：保留 public API 或兼容性约束；保留禁止提前编辑、限制变更
  范围等负向约束。
- **Decision update**：用后续决定替换 provisional 决定，同时保留没有被更新的其他决定；
  最终不能实现已被废弃的行为。
- **Entity binding**：为多个相似实体保留不同取值；后续改用 alias 引用时仍保持正确绑定。
- **Tool-result preservation**：使用只在早期工具输出中出现的 opaque 信息；从持久化的超大
  tool result 中恢复所需信息。

样例可以共享一套小型 rollback 代码，但每个 dataset record 必须在新的 workspace 运行。
hidden tests 应分别对应具体要求，避免只给整个 patch 一个无法解释的布尔结果。

## 构造规则

- 每个被评分的事实只引入一次，最终 turn 不再重复具体值。
- 使用 `C-11`、`D-21.rev2`、`TR-91` 等稳定且不敏感的标记支持确定性 trace 断言；
  语义正确性仍由行为测试判断。
- 信息引入与使用之间的 compact hop 数由样例声明；tool-result 首例要求至少一次，
  其他现有原子样例要求至少两次。
- 保持代码任务简单，并用明确测试捕获 entity 交换或旧 decision 被采用的情况。
- decision update 的 summary 可以记录旧决定已被替代；评分关注当前 revision 和最终行为。
- tool-result 样例需要从引入轮的目标 tool span 确认 Agent 确实通过工具输出获得了信息。

## 验收标准

- 原始 fixture 的目标回归测试失败，兼容性测试通过。
- reference patch 通过全部行为检查。
- 正式运行 benchmark 后，每个原子样例都应在其配置下激活预期 trace 路径；
  未运行真实 eval 的样例须标明激活尚未验证。
- 失败结果能够定位到类别和具体 preserve 检查。
