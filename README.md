# Standalone Code Agent

独立的 Python CLI 编码 Agent 项目，包含自己的源码、配置、依赖、锁文件和测试.

## 快速开始

在项目根目录运行：

```bash
cp .env.example .env
uv sync
uv run code-agent
```

启动前，在 `.env` 中填写 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL_NAME`。
Agent 默认提供本地读取、搜索、编辑、写入和 shell 工具。工具行为与可选 MCP
配置见[本地工具与 MCP](docs/usage.md)。

## 开发验证

完整测试与代码检查：

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

只运行指定模块时：

```bash
uv run python -m pytest tests/test_context_manager.py -q
uv run python -m pytest tests/test_skills.py -q
```

## 进一步阅读

- [本地工具与 MCP](docs/usage.md)：本地读取、shell 输出限制和远程工具配置。
- [追踪与遥测](docs/tracing.md)：Phoenix、本地 JSONL trace 与 span 字段。
- [Inspect 文件修改评测](docs/file-edit-evaluation.md)：样例、评分契约、追踪配置和运行命令。
- [上下文保留评测](docs/context-survival-benchmark/README.md)：上下文压缩后的信息保留评测。
