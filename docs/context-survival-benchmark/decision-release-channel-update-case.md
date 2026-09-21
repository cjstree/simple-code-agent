# `decision_release_channel_update` 样例设计

## 目的与 fixture

这个原子样例检查 Agent 在 compact 后采用最新决定，同时保留未被更新的决定。
独立 fixture 的两个公开发布构建函数通过 `release_policy.policy_for` 读取策略。
旁边还有使用同一策略表、但不发布为 release 的 draft preview 构建函数。
初始普通发布和 hotfix 都使用旧 channel、空 marker；preview 原本正确地使用
`pilot`。可见测试不写出最终发布取值；参考 patch 只修改策略表中普通发布和
hotfix 的两行。

| 检查 | 声明轮 | 当前决定 | 常见错误 |
| --- | ---: | --- | --- |
| `D-22` | 1 | 普通发布 channel=`staged`；两个公开发布构建函数使用 marker=`KITE-407` | 更新 hotfix 时丢失普通发布或 marker 的决定 |
| `D-21.rev2` | 2 | hotfix channel=`emergency`，替代第 1 轮暂定的 `pilot` | 沿用旧决定，或误用、改动 preview 的策略 |

七轮对话依次为：声明暂定及未变决定、更新 hotfix 决定、追踪发布策略和
draft preview、分析误用 preview 策略的候选方案、检查版本传递、规划验证、
按最终决定实现。
前六轮不编辑；最后一轮不重复任何精确取值。每个被评分的决定及标记只在声明轮出现。

## 测试与评分

`D-21.rev2` 的可见 FAIL_TO_PASS 检查两个 channel 不同，隐藏测试检查 hotfix
精确使用更新值；可见 PASS_TO_PASS 检查传入版本与 preview 的既有策略不变。
`D-22` 的可见 FAIL_TO_PASS
检查 marker 非空，隐藏测试检查普通 channel 和两个清单的 marker；可见
PASS_TO_PASS 检查普通清单的版本不变。初始 fixture 的 FAIL_TO_PASS 逐项失败、
PASS_TO_PASS 逐项通过；参考 patch 应全部通过。

两个检查的 `used_turn=7`、`min_compact_hops=2`，summary 分别要求保留
`D-21.rev2` 和 `D-22`。行为 selector 在各组内逐项评分，合计占每项检查的
0.50；激活和 summary 留存各占 0.25。样例设置
`compact_thresh_hold=2500`、`reserved_token=700`、`max_tool_res=3`。
这些参数尚未经真实 eval 验证，不能据此认定 compact 已激活。
