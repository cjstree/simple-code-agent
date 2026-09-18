# `constraint_rollback_compatibility` 样例设计

## 面试介绍

> 这个样例测的不是 Agent 会不会修一个 rollback bug，而是它能否在十轮调查、候选方案干扰和上下文压缩后，仍然选出并遵守早期声明的约束。评测把“约束是否经过 compact”“摘要是否提到约束”“最终代码是否真的遵守约束”分开检查，避免把普通编码能力当成 context survival。

它是 constraint preservation 的原子样例，定义在
[context_survival.jsonl](../../evals/context_survival.jsonl)。Agent 需要修复 rollback reason
未出现在 API 响应、且在 repository reload 后丢失的问题，同时遵守三个兼容性约束。

## Fixture：让局部修法产生真实冲突

样例使用[独立 fixture](../../evals/fixtures/constraint_rollback_compatibility/)，不改变现有
`rollback_reason_long_horizon` 的输入。代码链路保持简短：

```text
deployment_api.rollback_deployment
  → DeploymentService.rollback
  → DeploymentRepository.get/save
  → Deployment 模型
  → AuditLog.record
```

初始代码会把状态改为 `rolled_back`，但没有正确设置、保存或返回 rollback reason。
[API](../../evals/fixtures/constraint_rollback_compatibility/deployment_api.py) 还有一个
旧请求分支：请求缺少 `reason` 时，它调用 service 却不传该参数。这让“给 service 参数加
默认值”成为一个看似快捷的修法。

| 检查 | 早期声明的约束 | 看似方便但违约的修法 | 合格实现的行为 |
| --- | --- | --- | --- |
| C-11 | `DeploymentService.rollback(self, deployment_id: str, reason: str)` 的参数名、类型、调用方式及必填性保持不变 | 给 `reason` 加默认值 | API 为旧请求补 `unspecified`，再按原签名调用 service |
| C-12 | 无 `rollback_reason` 的旧行仍可读，保存时保留其他存储字段 | 直接索引新字段，或只用 model 字段重建整行 | 读取时为缺失字段提供缺省值，保存时保留原行的无关字段 |
| C-13 | 重复 rollback 保留首次 reason，只产生一次 `deployment_rolled_back` 事件 | 每次请求都覆盖 reason 并追加事件 | 已回滚时返回原结果，不再写入或审计 |

[可见测试](../../evals/fixtures/constraint_rollback_compatibility/tests/test_rollback.py)
展示 reason 响应、旧 API 请求和状态转换；
[隐藏测试](../../evals/hidden_tests/constraint_rollback_compatibility/test_constraint_regression.py)
分别检查签名、旧行读取、无关字段保留和重复调用。隐藏测试在 Agent 完成后才注入评分
workspace。在完成 reason 返回与持久化等功能修复后，若再给 service 的 `reason` 加默认值，
可见测试仍可通过，却会被 C-11
的隐藏签名测试抓住。[参考 patch](../../evals/reference_patches/constraint_rollback_compatibility.patch)
提供了一种满足全部检查的实现。

## 十轮对话：先声明，再干扰，最后引用

轮次在数据集的 `metadata.turns` 中定义，由 runner 在同一 Agent 会话里顺序执行。

| 轮次 | 作用 |
| --- | --- |
| 1 | 提出主任务，声明 C-11 和 C-12；要求先调查、不编辑。 |
| 2 | 声明 C-13；继续追踪 API、service、repository 和 audit。 |
| 3 | 阅读可见测试和旧请求形态，梳理 reason 丢失的位置。 |
| 4 | 分析“同事建议”的快捷方案：可选 service 参数、必填存储字段、整行覆盖、每次重复审计。这是待评估的候选方案，不是覆盖早期约束的新指令。 |
| 5–9 | 继续调查状态转换、存储行映射、请求形态、连续调用和验证边界；不再以约束条款形式重申 C-11/12/13。 |
| 10 | 仅要求“按前面的要求实现”并运行可见测试。 |

这段距离让 Agent 必须从历史中选择仍然有效的要求，并在候选方案与早期约束冲突时作出
正确取舍。前九轮都要求暂不编辑，使实际代码决策集中在最后一轮。

## Eval 执行流程

1. [Task](../../src/code_agent_evals/tasks.py) 从 JSONL 加载样例，把 fixture 放入独立的
   local sandbox。
2. [Solver](../../src/code_agent_evals/solver.py) 只把 `turns`、`agent_config` 和可选的
   restart 边界序列化给 runner；`context_survival.checks` 只供评分使用，不传给 Agent。
3. [Runner](../../src/code_agent_evals/runner.py) 配置 Agent session，并按顺序执行十轮。
   本样例设置 `compact_thresh_hold=4000`、`reserved_token=700`、`max_tool_res=3`。
   `runner_timeout_seconds=720` 只控制 solver 等待 runner 的上限。
4. Agent 结束后，[scorer](../../src/code_agent_evals/scorer.py) 读取 sandbox 中的
   `.eval_traces/`，注入隐藏测试，再检查 trace 和最终代码行为。

历史 compact 的触发条件是 `context_token + reserved_token >= compact_thresh_hold`，因此
当前配置约在活跃上下文达到 3300 token 时触发。实际次数取决于模型回复和工具调用，
不能仅由轮次数或配置推定。

## Scorer：路径、摘要和行为分别计分

每项检查声明 `introduced_turn`、`used_turn=10`、`min_compact_hops=2`、
`summary_contains` 和 `behavior_tests`。C-11/C-12 在第 1 轮引入，C-13 在第 2 轮引入。
scorer 对每项分别计算：

| 维度 | 权重 | 判定 |
| --- | ---: | --- |
| 路径激活 | 0.25 | 引入轮之后、使用轮结束前，至少有两次 `session.compact_history`。 |
| 摘要留存 | 0.25 | 路径已激活，且窗口内每次 compact 的 summary 都包含该项的 C 标记。 |
| 最终行为 | 0.50 | 逐个运行该项绑定的可见或隐藏 pytest selector，按通过比例计分。 |

每项分数为三部分之和；样例总分为三个检查分数的算术平均。只有三项检查都满足路径
激活、摘要留存和**全部**行为测试，顶层 `strict_pass` 才为 true。正常运行但 compact
不足两次会记为未激活；trace 缺失或损坏则是评测基础设施错误。

摘要中的标记只证明它提到了约束，不能单独证明语义正确；最终行为由测试验证。Agent
自己的“已完成”报告也不作为成功证据。

## 验证边界

基线 fixture 对每项检查都有失败的目标行为；参考 patch 通过全部检查。曾在
`compact_thresh_hold=1200` 下完成一次十轮真实运行，三个检查均为 `strict_pass=true`，
但该阈值使 compact 几乎每轮发生。当前阈值已改为 4000；**尚未用这个新阈值重新运行
真实 eval**，因此不能声称它一定满足至少两次 compact 或达到预期耗时。
