# Repository Guidelines

## Project Structure & Module Organization

This repository is a standalone Python 3.11+ CLI agent. Production code lives in
`src/code_agent/`: `agent.py` owns the interaction loop, `tool_registry.py` and
`tool.py` define local tool behavior, `mcp_tool.py` handles optional MCP tools,
and `telemetry.py` contains tracing support. Tests are in `tests/` and generally
mirror those modules (for example, `tests/test_mcp_tool.py`). The small
`usecase/` directory contains manual examples. Project metadata and dependency
groups are defined in `pyproject.toml`; keep `uv.lock` synchronized with it.

## Build, Test, and Development Commands

- `uv sync` installs runtime and development dependencies from the lockfile.
- `uv run code-agent` starts the interactive CLI using the configured `.env`.
- `uv run pytest` runs the complete test suite under `tests/`.
- `uv run pytest tests/test_agent_loop.py -q` runs one focused test module.
- `uv run ruff check .` checks lint rules and import/style issues.
- `uv run ruff format --check .` verifies formatting without changing files.
- `uv sync --extra telemetry` installs optional Phoenix tracing dependencies.

## Coding Style & Naming Conventions

Use four-space indentation, Python type annotations, and Ruff's 88-character
line length with Python 3.11 syntax. Follow standard Python naming: `snake_case`
for functions, variables, and modules; `PascalCase` for classes; and
`UPPER_SNAKE_CASE` for constants. Keep async boundaries explicit and prefer
small, single-purpose functions. Run Ruff checks before submitting changes.

## Testing Guidelines

Tests use `pytest` and `pytest-asyncio`. Name files `test_<feature>.py` and test
functions `test_<behavior>`. Mark coroutine tests with `@pytest.mark.asyncio`.
Use fixtures such as `monkeypatch` and lightweight fakes to isolate API, MCP,
approval, and telemetry behavior; tests should not require live services or real
credentials. Add regression coverage for every behavior change. No numeric
coverage threshold is configured, so prioritize meaningful branch coverage.

Each newly add test function should be add simple comment under the func to describe
the test scenario.

## Commit & Pull Request Guidelines

The current history is minimal and uses short, sentence-case summaries (for
example, `Project initialize. Move code from rag-agent.`). Keep commits focused
and write an imperative summary that explains the change. Pull requests should
describe the motivation and behavior, list validation commands run, link related
issues, and call out configuration or dependency changes. Include terminal
output or screenshots only when CLI presentation changes.

## Security & Configuration

Copy `.env.example` to `.env` and set `LLM_API_KEY`, `LLM_BASE_URL`, and
`LLM_MODEL_NAME`; never commit secrets. Treat `MCP_URL` and Phoenix tracing as
optional integrations, and preserve approval checks around mutating or shell
tools.

## User Override

If the user's instructions conflict with any rule in this document, ask for explicit confirmation before overriding. Only then execute their instructions.
