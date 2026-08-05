阶段 0：MCP + Simple Agent Loop 闭环

**时间：1～2 周**

### 目标

```text
User
 → Agent Loop
 → LLM Tool Call
 → MCP Client
 → MCP Server
 → Tool Result
 → Agent Loop
 → Final Answer
```

### 必须完成

- MCP tool discovery；
- JSON Schema 参数解析与校验；
- 单工具和连续多工具调用；
- max steps；
- timeout；
- tool error 进入 observation；
- final answer；
- 基础 streaming；
- 5～10 个固定自动化测试任务。

### Python 目标

通过该闭环掌握：

- `asyncio`、`async/await`；
- Pydantic；
- Protocol / ABC；
- 类型注解；
- async context manager；
- pytest 与 async test；
- 异常传播和资源清理。

### 退出标准

- 无工具、单工具、多工具、工具失败、max-step 五类用例可重复执行；
- README 可以让他人启动；
- 核心 loop 能由我独立解释和局部重写；
- 不加入 Memory、Multi-agent、Planner 和复杂 UI。