# `entity_route_alias_binding` 样例设计

## 目的与 fixture

这个原子样例检查 Agent 能否保持相似实体各自的取值，并在后来改用 alias
引用时仍保持配对。独立 fixture 提供 `Route(target_id, bucket)`、
`resolve_route(target)`、被 resolver 使用的目录，以及管理界面的选择列表。
目录已有正常工作的 APAC／`line-c` 路由；EU、US 的 bucket 仍是占位值，
`line-a`、`line-b` 还未绑定。未知名字已抛出 `KeyError`。

| 检查 | 声明轮 | 目标与 alias | bucket |
| --- | ---: | --- | --- |
| `E-31` | 1 | `invoice-api-eu` ↔ `line-b` | `violet-17` |
| `E-32` | 2 | `invoice-api-us` ↔ `line-a` | `amber-42` |

两个 alias 与目标的字母顺序故意交叉；管理界面分别排序目标和 alias 供展示，
这两个列表不定义配对。七轮对话依次为：声明两项绑定、调查目录／resolver／
管理界面、分析按展示顺序配对的错误候选方案、比较规范名与 alias 调用、
规划验证、按早期绑定实现。前六轮不编辑；后续轮次不重述具体配对或 bucket。
可见测试只要求 alias 解析为某个规范目标，不揭示实际配对。

## 测试与评分

每项的可见 FAIL_TO_PASS 检查对应 alias 被规范化，隐藏 FAIL_TO_PASS
同时检查规范名和 alias 返回精确的 `target_id` 与 bucket。`E-31` 的可见
PASS_TO_PASS 检查未知名字仍抛 `KeyError`，隐藏测试检查 APAC 路由不变；
`E-32` 的隐藏 PASS_TO_PASS 检查直接使用规范名时目标 ID 不变，可见测试检查
管理界面仍提供分别排序的选择列表。初始 fixture 的 FAIL_TO_PASS 逐项失败、
PASS_TO_PASS 逐项通过；参考 patch 应全部通过。交换两个 alias 的错误修法会被
隐藏测试抓住。

两个检查的 `used_turn=7`、`min_compact_hops=2`，summary 分别要求保留
`E-31` 和 `E-32`。行为测试占 0.50，激活和 summary 留存各占 0.25。
样例设置 `compact_thresh_hold=2500`、`reserved_token=700`、`max_tool_res=3`；
尚未运行真实 eval 验证 compact 激活。
