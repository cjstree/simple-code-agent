# Standalone Code Agent

This directory is an independent Python project. It owns its source code,
configuration, dependencies, lock file, and tests; it does not import any module
from the parent RAG backend.

## Setup

```bash
cd agent
cp .env.example .env
uv sync
uv run code-agent
```

Set `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL_NAME` in `.env`. The agent starts
with local read, search, edit, write, and shell tools. Each mutating or shell tool
call asks for approval.

MCP integration is optional. Set `MCP_URL` to any Streamable HTTP MCP endpoint,
for example `http://127.0.0.1:8000/mcp`, to discover and add its remote tools. With
`MCP_URL` unset, the agent has no runtime dependency on the RAG backend.

Phoenix tracing is also optional:

```bash
uv sync --extra telemetry
```

Then enable `PHOENIX_ENABLED` and configure its endpoint in `.env`.

## Validation

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

# Test

  uv run python -m pytest -q

  只运行 ContextManager 测试：

  uv run python -m pytest tests/test_context_manager.py -q

  只运行 skills 测试：

  uv run python -m pytest tests/test_skills.py -q