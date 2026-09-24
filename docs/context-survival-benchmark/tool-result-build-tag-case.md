# `tool_result_build_tag` 样例设计

## 目的与输入

这个原子样例检查 Agent 能否保留只在早期工具输出中明文出现的构建标记，并在最后一轮
用于发布清单。独立 fixture 的 `snapshot_probe.py` 从固定快照字段计算标记，运行时输出
`TR-91` 和精确 `build_tag`；fixture 源码和后续用户提示均不明文给出该值。
`release_policy.py` 中发布清单的 tag 初始为空，preview 使用独立且已工作的策略。

八轮依次为：运行探查脚本并检查目标代码、调查构建函数和可见测试、两轮只读库存小任务、
复核 preview 边界、规划验证、最终实现。第 2–7 轮不重复探查结果，前七轮不编辑。
库存任务只用于增加工具交互和信息间隔，不影响发布清单。

## 评分与验证

单项检查 `TR-91` 的 `category=tool_result`、`introduced_turn=1`、`used_turn=8`、
`min_compact_hops=1`。除了要求窗口内每次 `session.compact_history` 的 summary 保留
标记和精确值，评分器还要求第 1 轮名为 `bash` 的 tool span 输出包含二者。
`trace_preserved` 只有在来源和摘要均满足时才通过；混合分仍为激活 0.25、摘要留存
0.25、行为测试 0.50。

可见 FAIL_TO_PASS 检查发布 tag 非空，隐藏 FAIL_TO_PASS 检查精确值；两个可见
PASS_TO_PASS 检查发布清单的版本和 channel、preview 的既有策略。原始 fixture 的
目标测试失败，兼容性测试通过；参考 patch 应通过全部测试。样例设置
`compact_thresh_hold=2500`、`reserved_token=700`、`max_tool_res=3`。

构造阶段只运行 fixture、参考 patch 和合成 trace 测试，**尚未运行真实 eval**。
实际 compact 激活次数须在后续运行中依据 trace 观察；当前配置不保证触发。
