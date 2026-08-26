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

## Test commands

```bash
uv run python -m pytest -q
```

只运行 ContextManager 测试：

```bash
uv run python -m pytest tests/test_context_manager.py -q
```

只运行 skills 测试：

```bash
uv run python -m pytest tests/test_skills.py -q
```

## Inspect 文件修改评测

文件修改评测定义在 `src/code_agent_evals/`，样例清单位于
`dataset/file_edit.jsonl`，样例的初始文件位于 `dataset/fixtures/`。

评测使用 Inspect `local` sandbox。每个样例运行时，Inspect 会创建独立的
临时目录，将样例文件复制到该目录，以该目录作为工作目录启动 Agent，最后在
同一目录运行 pytest。样例结束后临时目录会被删除，因此 Agent 不会修改
`dataset/fixtures/` 中的原始文件。

`local` sandbox 只提供独立临时工作目录，不是安全边界。Agent 执行的命令仍然
运行在宿主机上，这套配置只用于可信的轻量端到端测试。

### 模型与追踪配置

Eval runner 不使用 Inspect model bridge。Agent 按常规 CLI 配置直接连接模型，
因此在项目 `.env` 中设置 `LLM_API_KEY`、`LLM_BASE_URL` 和
`LLM_MODEL_NAME` 即可；`INSPECT_EVAL_MODEL` 不控制 Agent 使用的模型。

如需在 Phoenix 中观察 eval，继续使用 Agent 自身的追踪配置：

```dotenv
PHOENIX_ENABLED=true
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006/v1/traces
PHOENIX_PROJECT_NAME=code-agent-eval
```

Inspect 仍负责创建样例工作目录、执行 solver 和运行 pytest scorer，但模型调用、
token 配置和 Phoenix telemetry 均由 Agent 自己负责。

### 样例目录结构

每个样例使用一个独立目录，目录名建议与 sample ID 一致：

```text
dataset/
├── file_edit.jsonl
└── fixtures/
    └── fix_multiply_with_context/
        ├── calculator.py
        ├── tests/
        │   └── test_calculator.py
        ├── skills/
        │   └── arithmetic-fix/
        │       └── SKILL.md
        └── memory/
            ├── MEMORY.md
            └── project-arithmetic.md
```

源码和 `tests/` 是常规样例内容。`skills/` 和 `memory/` 是可选目录；由于
Agent 在样例根目录启动，现有逻辑会自动扫描 `./skills` 并读取 `./memory`。

Skill 使用 Agent 当前支持的 frontmatter 格式：

```markdown
---
name: arithmetic-fix
description: Diagnose and verify arithmetic fixes
---

Read the implementation and tests before editing, then run pytest.
```

Memory 的 `MEMORY.md` 是索引：

```markdown
- [0]:[Project Convention](project-convention.md) — Project-specific guidance
```

索引指向的 memory 文件包含 frontmatter 和正文：

```markdown
---
name: Project Convention
description: Project-specific guidance
type: project
---

Memory content used by the Agent.
```

### 在 JSONL 中注册样例

在 `dataset/file_edit.jsonl` 追加一行 JSON：

```json
{"id":"fix_example","input":"Fix the implementation and run the tests.","target":"","files":{".":"fixtures/fix_example"}}
```

多轮样例把连续用户输入放在 `metadata.turns` 中。所有轮次复用同一个
Agent 和消息历史；`agent_config` 可以降低 eval 中的 compact 阈值：

```json
{"id":"compact_example","input":"Complete the multi-turn task.","target":"","metadata":{"turns":["Inspect the implementation; do not edit yet.","Preserve the public API.","Now fix it and run the tests."],"agent_config":{"max_messages":8,"context_limit":2000}},"files":{".":"fixtures/compact_example"}}
```

`agent_config` 仅支持 `context_limit`、`max_messages`、`max_tool_res`、
`max_tool_round_res` 和 `persist_threshold`，值必须为正整数。省略
`metadata.turns` 时，runner 仍将 `input` 作为单轮任务执行。

字段含义：

- `id`：样例唯一标识，用于 `--sample-id` 选择样例。
- `input`：提交给 Agent 的任务描述。
- `metadata.turns`：可选的连续用户回合；同一样例内共享 Agent 状态。
- `metadata.agent_config`：可选的 eval 专用 ContextManager 阈值。
- `target`：当前 pytest scorer 不使用文本 target，可以保留为空字符串。
- `files`：sandbox 目标路径到源文件或目录的映射。
- `files` 中的 `"."` 表示把 fixture 目录内容复制到样例工作目录根部。
- fixture 路径相对于 `dataset/file_edit.jsonl` 所在目录解析。

构造新样例的推荐步骤：

1. 在 `dataset/fixtures/<sample-id>/` 创建一份最小项目。
2. 放入带有明确错误的实现和能够暴露该错误的 pytest 测试。
3. 如有需要，添加 `skills/<skill-name>/SKILL.md`。
4. 如有需要，添加 `memory/MEMORY.md` 及其引用的 memory 文件。
5. 在 `dataset/file_edit.jsonl` 追加对应 JSON 记录。
6. 使用 `--sample-id <sample-id>` 单独运行并检查结果。

不要把密钥、真实用户 memory 或依赖外部服务的测试放进 fixture。测试应当可重复，
且不依赖 Agent 最终回答的措辞；当前 scorer 通过 `python -m pytest -q` 的退出状态
评分。

### 启动评测

运行全部样例：

```bash
uv run inspect eval \
  src/code_agent_evals/tasks.py@basic_agent_eval \
  --max-samples 1 \
  --max-tasks 1
```

只运行一个样例：

```bash
uv run inspect eval \
  src/code_agent_evals/tasks.py@basic_agent_eval \
  --sample-id fix_multiply_with_context \
  --max-samples 1 \
  --max-tasks 1
```

命令中的 `src/code_agent_evals/tasks.py@basic_agent_eval` 由两部分组成：

- `src/code_agent_evals/tasks.py` 指定包含评测任务的 Python 文件。
- `@basic_agent_eval` 指定该文件中由 `@task` 注册的任务函数。

当一个 Python 文件包含多个 Inspect Task 时，`@<task-name>` 可以只运行指定任务。
示例保留 `--max-samples 1` 和 `--max-tasks 1`，方便逐个观察 Agent 行为；
不再存在 bridge 固定代理端口的限制。
