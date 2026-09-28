# 综合长程样例：mixed_release_long_horizon

## 目标与输入

第五例将四个独立 fixture 的业务代码、可见测试、隐藏测试和参考补丁复制到一个独立
workspace。覆盖原有全部 8 项 check：C-11/12/13、D-21.rev2/D-22、E-31/32、TR-91。
唯一模块冲突是两份 `release_policy.py`：snapshot 模块改名为 `snapshot_policy.py`，
同步改动其测试 import；业务含义不变。两套 manifest 的 preview 行为各自保留。

稳定 ID 为 `mixed_release_long_horizon`；数据集中的 input 与 24 个 turns 构成完整输入。
预期结果是从第一次实现起，四类合同在每次后续维护和最终交付时都成立。
回归目的包括旧决定回流、实体串线、丢失工具事实和维护过程破坏先前约束。

| 轮次 | 任务 |
| --- | --- |
| 1–7 | 交错引入约束、初始决定、实体绑定、真实 probe 输出、决定修订 |
| 8–11 | 跨模块调查、错误候选方案、inventory 干扰 |
| 12 | 首次综合实现；保存第一个 checkpoint |
| 13–17 | 检查邻近行为、inventory 切换、旧决定和错误存储方案干扰 |
| 18 | 综合维护；保存第二个 checkpoint |
| 19–23 | 再次切换任务、共享 policy 提案、兼容性和重试审查 |
| 24 | 最终交付；保存第三个 checkpoint |

后续实现轮不重复具体 channel、alias、bucket 或 build tag，要求利用早期上下文。
允许工作笔记；probe 要求仅首次运行。它衡量带工具和工作区的 Agent 整体表现，
不是排除所有外部记忆之后的纯摘要记忆实验。

## 主评分：long_horizon_v1

主分只使用各 checkpoint 的实际行为测试结果：

1. 每个 check 内 selector 等权平均（复用原子例的 FAIL_TO_PASS/PASS_TO_PASS 测试）。
2. 每个类别内 check 等权平均。
3. constraint、decision_update、entity_binding、tool_result 四类各占 25%。
4. 第 12、18、24 轮各占最终主分的 1/3。

这样 constraint 有三项 check 也不会比其他类别占更高权重。
`Score.value` 范围为 0～1；`strict_pass` 表示所有 checkpoint 的行为全部正确。
最后修好不会抹去中途失败，也不会只凭 summary 中写出了标记而得分。
原子例继续使用已有的版本 1 混合评分。

报告位于 `metadata.context_survival`：

- `checkpoints`：逐轮总分、四类分数、逐 check 和逐 selector 的通过情况。
- `final_score`、`worst_checkpoint_score`：终态质量与最差阶段质量。
- `regressions`：相邻 checkpoint 中每个 check/selector 的 pass→fail 事件。
- `drift_rate`：上述事件数 / 上一个 checkpoint 已通过的 check/selector 数。
  分母跨两次转换累加；分母为零时记 0，同时报告 `drift_opportunities=0`。
  因而一直失败的 agent 可能漂移率为零，必须同时阅读主分。
- `diagnostics`：实际 compact 次数、摘要标记留存、真实工具来源是否观测到。
- `compact_exposed`：每项检查的引入到使用窗口中是否至少经历一次历史 compact。

compact 次数、字面标记和工具来源证据不贡献主分，也不决定行为 `strict_pass`。
没有发生 compact 的运行仍有有效行为分，但不能用来宣称 compact 后表现；必须连同
`compact_exposed` 报告。缺失或损坏 trace/checkpoint 属于基础设施错误。

## 快照与证据边界

Runner 在对应 `agent.run` 返回后，将 Python 源文件内容写到 workspace 外的 runtime
目录；solver 在清理该目录前把它们传入 `TaskState.store` 的
`code_agent_eval.checkpoints.v1`。只向 runner 发送 checkpoint 轮次，不发送评分 selector、
期望值或隐藏测试。失败、取消时沿用原有 runtime 清理流程。

评分器在评测结束后用每份快照重建独立目录，再注入仓库中原始可见测试和隐藏测试，
逐 selector 执行；不同阶段互不共享导入缓存。Agent 对可见测试的修改不能放宽判定。
快照仅支持本例的 Python 源文件，排除 tests、隐藏目录和缓存，拒绝符号链接；不适用于
依赖非 Python 运行时资源的通用任务。测试收集时因 Agent 代码损坏失败也算行为失败。

检查点之间发生又被修复的瞬时漂移不会被发现；调查轮是否完全只读、probe 是否被偷偷
重算也不纳入主分。因此这是阶段性业务合同遵守指标，不是所有过程指令的完整审计。

## compact 配置与运行

配置为 `compact_thresh_hold=6000`、`reserved_token=1000`、`max_tool_res=3`，
约 5000 token 的活跃上下文预算。给四个工作流留出空间，同时通过 24 轮自然调查制造
上下文压力；不强制每轮 compact，也不以达到某个次数为任务目标。
Runner 超时 3600 秒（60 分钟），用于覆盖完整 24 轮 Agent 执行。实际 compact 激活、模型质量和耗时需要真实 eval 验证。

```bash
uv run inspect eval src/code_agent_evals/tasks.py@context_survival_eval \
  --sample-id mixed_release_long_horizon
```

建议用相同模型及版本分别运行本配置和足够大的 compact 阈值作为对照，多次运行并同时
报告行为分、漂移率与实际 exposure；阈值较大不保证绝对无 compact，仍以 trace 为准。
目前完成的是确定性 fixture/参考补丁/评分回归验证，未宣称真实模型跑分或阈值已校准。
