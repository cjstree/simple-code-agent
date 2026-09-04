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

The local read tool numbers lines and uses a 1-indexed `offset` with an optional
line `limit`. To keep large files out of the model context, each result is capped
at 2,000 lines or 50 KiB and includes the next `offset` when more text is
available.

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
`evals/file_edit.jsonl`，样例的初始文件位于 `evals/fixtures/`。

评测使用 Inspect `local` sandbox。每个样例运行时，Inspect 会创建独立的
临时目录，将样例文件复制到该目录，以该目录作为工作目录启动 Agent，最后在
同一目录运行 pytest。样例结束后临时目录会被删除，因此 Agent 不会修改
`evals/fixtures/` 中的原始文件。

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
evals/
├── file_edit.jsonl
├── fixtures/
│   └── l2_expired_cache/
│       ├── cache.py
│       ├── models.py
│       ├── service.py
│       └── tests/
│           └── test_cache.py
└── hidden_tests/
    └── l2_expired_cache/
        └── test_cache_regression.py
```

源码和 `tests/` 是常规样例内容。`skills/` 和 `memory/` 是可选目录；由于
Agent 在样例根目录启动，现有逻辑会自动扫描 `./skills` 并读取 `./memory`。
`hidden_tests/` 不包含在 sample 的 `files` 映射中。Agent 完成后，scorer 才把
对应目录复制为 sandbox 内的 `.eval_hidden_tests/`。

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

在 `evals/file_edit.jsonl` 追加一行 JSON：

```json
{"id":"fix_example","input":"Fix the implementation and run the tests.","target":"","files":{".":"fixtures/fix_example"}}
```

多轮样例把连续用户输入放在 `metadata.turns` 中。所有轮次复用同一个
Agent 和消息历史；`agent_config` 可以降低 eval 中的 compact 阈值：

```json
{"id":"compact_example","input":"Complete the multi-turn task.","target":"","metadata":{"turns":["Inspect the implementation; do not edit yet.","Preserve the public API.","Now fix it and run the tests."],"agent_config":{"compact_thresh_hold":2000}},"files":{".":"fixtures/compact_example"}}
```

`agent_config` 仅支持 `compact_thresh_hold`、`max_tool_res`、
`max_tool_round_res`、`persist_threshold` 和 `reserved_token`，值必须为正整数。
省略 `metadata.turns` 时，runner 仍将 `input` 作为单轮任务执行。

需要验证持久化 memory 跨 Agent 接力时，可以使用
`metadata.restart_agent_after_turns`。其中的数字是从 1 开始的轮次编号；runner
会在对应轮次完成（包括 Stop hook）后关闭当前 Agent，再在同一 sandbox 和工作目录
启动新 Agent。最后一轮不能配置重启。例如下面会让第二轮由新 Agent 执行，而第一轮
写入的 `./memory` 仍然保留：

```json
{"metadata":{"turns":["Learn the project convention.","Apply the saved convention."],"restart_agent_after_turns":[1]}}
```

`restart_agent_after_turns` 只改变 eval runner 的实例生命周期，不修改 Agent 的生产
行为。新 Agent 不继承旧 Session 消息，但会重新扫描同一工作目录中的 skills 和
memory，并重新应用该样例的 `agent_config`。

### 复合能力样例

`rollback_reason_long_horizon` 是五轮真实模型场景。缺陷调查、旧数据兼容、实施方案和
幂等审计约束被分散到不同轮次，较低的 Session 阈值用于触发重复 history compaction；
最终 visible/hidden tests 同时检查 API、service、repository 和 audit 的协调结果。
运行时可通过 trace 中重复出现的 `session.compact_history` 人工确认压缩链路。

`release_policy_memory_handoff` 是两个 Agent episode 组成的真实模型场景。第一个 Agent
只接收源码中不存在的 release 约定并在 Stop hook 中抽取 memory；runner 随后重启
Agent。第二个 Agent 必须从磁盘 memory 召回约定、接受当前 hotfix 对其中一个字段的
覆盖，并在 history compaction 后完成实现。可通过 `memory.extract`、
`memory.select_relevant` 和 `session.compact_history` trace 人工检查完整链路。

这两个样例都使用普通的小型 Python fixture，不依靠大文件填充上下文。评分仍以
Agent 完成后的 visible/hidden pytest 行为为准；trace 目前用于人工确认预期能力路径。

mini-SWE 样例还要定义行为级测试契约：

```json
{
  "metadata": {
    "level": "L2",
    "regression_purpose": "Expired entries must behave as cache misses.",
    "FAIL_TO_PASS": [
      "tests/test_cache.py::test_expired_profile_is_a_cache_miss",
      ".eval_hidden_tests/test_cache_regression.py::test_entry_expiring_now_is_a_cache_miss"
    ],
    "PASS_TO_PASS": [
      "tests/test_cache.py::test_live_profile_is_returned",
      ".eval_hidden_tests/test_cache_regression.py::test_missing_and_unrelated_live_entries_keep_their_behavior"
    ]
  }
}
```

`FAIL_TO_PASS` 是原始实现失败、修复后必须通过的行为；`PASS_TO_PASS` 是修复前后
都必须通过的兼容行为。两组都必须非空、不能重叠，并且只能引用 `tests/` 或
`.eval_hidden_tests/` 下的 pytest node ID。最终得分是两组结果的逻辑与。

字段含义：

- `id`：样例唯一标识，用于 `--sample-id` 选择样例。
- `input`：提交给 Agent 的任务描述。
- `metadata.turns`：可选的连续用户回合；同一样例内共享 Agent 状态。
- `metadata.restart_agent_after_turns`：可选的 1-based 轮次列表；在指定轮次后
  关闭并重建 Agent，但保留 sandbox 工作目录。
- `metadata.agent_config`：可选的 eval 专用 Session 阈值。
- `metadata.level`：case 难度，例如 `L2` 或 `L3`。
- `metadata.reference_patch`：用于验证 case 可解性和变更层级的参考补丁；不参与评分。
- `metadata.regression_purpose`：case 覆盖的行为和回归目的。
- `metadata.FAIL_TO_PASS`：原始实现失败、修复后应通过的测试清单。
- `metadata.PASS_TO_PASS`：原始实现及修复后都应通过的测试清单。
- `target`：当前 pytest scorer 不使用文本 target，可以保留为空字符串。
- `files`：sandbox 目标路径到源文件或目录的映射。
- `files` 中的 `"."` 表示把 fixture 目录内容复制到样例工作目录根部。
- fixture 路径相对于 `evals/file_edit.jsonl` 所在目录解析。

构造新样例的推荐步骤：

1. 在 `evals/fixtures/<sample-id>/` 创建一份最小项目和 visible tests。
2. 在 `evals/hidden_tests/<sample-id>/` 添加同一 specification 的回归测试。
3. 在原始 fixture 上确认每个 `FAIL_TO_PASS` 失败、每个 `PASS_TO_PASS` 通过。
4. 在 `evals/file_edit.jsonl` 注册稳定 ID、输入、level、回归目的和测试清单。
5. 在 `evals/reference_patches/` 添加 reference fix，并确认两组测试全部通过。
6. 使用 `--sample-id <sample-id>` 单独运行并检查结果。

不要把密钥、真实用户 memory 或依赖外部服务的测试放进 fixture。测试应当可重复，
且不依赖 Agent 最终回答的措辞。旧样例仍执行整个 pytest suite；带测试契约的
mini-SWE 样例按 `FAIL_TO_PASS` 和 `PASS_TO_PASS` 的外部可观察结果评分。

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
