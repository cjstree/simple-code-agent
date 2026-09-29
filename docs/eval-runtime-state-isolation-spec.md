# Eval 运行时状态隔离 Spec

## 状态

本文定义 eval 中 trace 与 memory 的最终落盘和传递方案。该方案已实现；后续修改应同步
更新测试、README 和 trace 输入契约。

## 背景

Inspect `local` sandbox 会为每个 sample 创建独立工作目录，但它不是文件系统安全边界。
当前 eval runner 在 Agent 工作目录中写入 `.eval_traces/`，Agent 可以通过普通文件工具
直接读取或修改评分证据。fixture 中的 `memory/` 也位于同一工作目录，Agent 可以绕过
`Memory.select_relevant_memories`，直接读取完整 memory 文件。

本方案把这两类 eval 运行时状态移出 Agent 工作目录，并保持以下行为：

- scorer 仍能使用完整 trace；
- Agent 只能通过 memory 选择和注入链路获得相关 memory；
- 同一 sample 内重启 Agent 时，已提取的 memory 能继续使用；
- 不改变非 eval 场景中 Agent 默认使用 `./memory` 和常规 trace 配置的行为。

## 安全边界

本方案基于一个明确假设：被测 Agent 不主动读取工作目录之外的绝对路径。它用于阻止
工作目录内的偶然发现和直接读取，不提供针对恶意宿主机访问的强隔离。

在 Inspect `local` sandbox 下，Agent 与 eval runner 使用同一宿主机用户。绝对路径、
随机目录名和目录权限都不能构成真正的安全边界。若未来需要抵抗主动遍历 `/tmp`、读取
进程信息或访问宿主机文件，应改用容器边界和 Agent 不可读取的外部 trace collector。

## 最终目录布局

Solver 为每个 sample 创建唯一的绝对临时根目录：

```text
/tmp/code-agent-eval-<random>/
├── traces/
│   ├── manifest.jsonl
│   └── trace_log_<session-id>.jsonl
└── memory/
    ├── MEMORY.md
    └── <memory-name>.md
```

具体系统临时目录由 `tempfile.mkdtemp()` 决定，不硬编码 `/tmp`。随机目录不得在不同
sample 或 epoch 之间复用。`traces/` 与 `memory/` 必须是独立子目录。

## 组件职责

### Solver

Solver 是临时根目录的唯一生命周期所有者，负责：

1. 使用 `tempfile.mkdtemp()` 创建每次运行唯一的绝对目录；
2. 将该目录作为 eval 内部字段加入 runner payload；
3. 启动 runner，并保持 Agent 最终回答与诊断输出分离；
4. runner 成功退出后，从 `traces/` 读取 manifest 及其引用的文件；
5. 将原始、可 JSON 序列化的 trace bundle 写入 `TaskState.store`；
6. 在所有成功、失败、超时和取消路径的 `finally` 中删除整个临时根目录。

临时目录只能由 Solver 生成，不能从 dataset metadata、sample prompt 或其他 Agent
可控输入接受。建议使用带版本的 store key，例如：

```text
code_agent_eval.trace.v1
```

其值为：

```json
{
  "manifest": "<manifest JSONL>",
  "files": {
    "trace_log_<session-id>.jsonl": "<trace JSONL>"
  }
}
```

Solver 必须在写入 store 后立即清理磁盘目录，不把临时绝对路径交给 scorer，也不依赖
scorer 执行清理。Runner 失败时，Solver 应保留现有 stdout/stderr 诊断并将其归类为基础
设施失败，同时仍然清理临时目录。

### Runner

Runner 负责使用临时目录，但不拥有其最终清理：

1. 解析 Solver 生成的临时根目录并要求其为绝对路径；
2. 在创建第一个 Agent 之前准备 `traces/` 和 `memory/`；
3. 如果 Agent 工作目录存在 fixture `memory/`，将其移动到临时根目录的 `memory/`，
   不在工作目录保留副本；
4. 将 `traces/` 传给 eval telemetry，将 `memory/` 传给每一个 Agent 实例；
5. Agent restart 前等待旧 Agent 完整关闭，再用同一个 `memory/` 创建新 Agent；
6. 最终 Agent 关闭且 telemetry shutdown/flush 完成后，runner 才返回成功。

若工作目录中的 `memory` 存在但不是目录，或外部目标目录已包含非预期内容，runner 应
报告基础设施错误，而不是覆盖文件或继续运行。移动 fixture memory 发生在 Agent 启动
之前，保证 Agent 从第一轮开始只能走 memory 注入链路。

### Agent 与 Memory

Agent 增加可选的 memory 路径配置，并保持生产默认值为 `Path("./memory")`。eval runner
显式传入临时目录中的 `memory/`。

`Memory` 已经以构造参数 `path` 为所有索引、选择、读取和提取写入的根目录；实现不应
再引入其他 cwd 相对路径。以下生命周期必须保持：

- 每轮由 `inject_memory()` 调用 `select_relevant_memories()`；
- 主 Agent 只看到被选择后注入 session 的 memory 正文；
- Agent 关闭时等待已排队的 memory extraction；
- restart 后的新 Agent 使用同一外部 memory 路径，因此可以读取上一实例提取的内容；
- 不同 sample 的 memory 永不共享，eval 结束后全部删除。

fixture memory 属于 eval harness 输入，不是被评分项目的输出。迁移后工作目录不会保留
`memory/`。若未来某个 case 需要把项目自身的 `memory/` 作为普通文件进行编辑或测试，
该 case 必须使用不同目录名或显式选择不启用本隔离机制。

### Scorer

Scorer 不再从 sample sandbox 的 `.eval_traces/` 读取 trace，也不接触临时目录路径。
它从 `TaskState.store` 获取 trace bundle，并调用现有 `parse_trace_jsonl()` 完成格式校验、
turn 重建和评分。

以下情况仍属于基础设施失败，而不是普通零分：

- store 中没有预期版本的 trace bundle；
- manifest 或引用文件缺失；
- JSONL、schema、session/span 关系或路径字段不合法；
- runner 未完成 telemetry flush。

不声明 trace 评分契约的 legacy sample 可以不读取 bundle，但 Solver 的临时目录清理规则
不因此改变。

## 执行时序

```text
Solver 创建随机临时根目录
  -> Runner 将 workspace/memory 移至 temp/memory
  -> Runner 初始化 telemetry，写入 temp/traces
  -> Agent(memory_path=temp/memory) 执行各轮
  -> restart 时关闭旧 Agent，并复用 temp/memory 启动新 Agent
  -> 最终 Agent 关闭，memory extraction 完成
  -> telemetry shutdown/flush
  -> Runner 返回最终回答
  -> Solver 读取并校验 trace 输入文件
  -> Solver 将 trace bundle 写入 TaskState.store
  -> Solver 删除整个临时根目录
  -> Scorer 从 TaskState.store 解析并评分
```

最终回答继续进入 `state.output`；trace bundle 只进入 `state.store`，不得拼接进模型回答、
runner 诊断或下一轮 Agent 上下文。

## 失败与清理要求

- 临时根目录必须按 sample 创建，避免并发运行串写 manifest、trace 或 memory。
- runner 启动失败、模型失败、工具失败导致 runner 非零退出、timeout 和 cancellation 都
  必须触发 Solver 清理。
- Agent restart 期间不能清理 memory；只有整个 runner 生命周期结束后才能删除。
- Solver 应先完整读取 trace bundle，再删除磁盘目录；读取失败应作为基础设施错误上抛。
- 清理不应覆盖原始错误。清理自身失败应进入诊断信息，便于发现宿主机临时文件泄漏。
- 不使用固定共享目录，也不依赖进程退出或 Python GC 自动回收。

## 兼容性

该方案当前仅承诺支持 Inspect `local` sandbox。它依赖 Solver 和 runner 看到同一个宿主机
绝对路径。未来切换 Docker 或远程 sandbox 时，必须改为共享挂载、runner 结果协议，或
外部 trace collector，不能假设宿主机绝对路径在 sandbox 内可见。

生产 CLI 和非 eval 调用保持以下默认行为：

- 未显式传入 memory 路径时继续使用 `./memory`；
- 常规 `TRACE_LOG_DIR` 行为不变；
- eval 专用临时根目录不加入全局 settings，不通过环境变量在不同并发 sample 间共享。

## 验证要求

实现时至少增加以下测试：

1. Solver 为并发 sample 创建不同的绝对临时目录；
2. runner payload 不接受 dataset metadata 提供临时路径；
3. fixture memory 在 Agent 启动前被移出工作目录，原位置不保留副本；
4. Agent 通过注入获得选中的 memory，而工作目录中不存在可直接读取的 memory 文件；
5. restart 后的新 Agent 能使用上一实例提取到同一外部目录的 memory；
6. trace 只写入外部 `traces/`，工作目录中不生成 `.eval_traces/`；
7. Solver 将完整 trace bundle 写入 store，scorer 能解析并得到原有评分结果；
8. 正常完成、runner 失败、trace 损坏、timeout 和 cancellation 后临时目录均被清理；
9. 缺失或损坏的 store trace 继续报告基础设施错误；
10. 非 eval Agent 未传路径时仍使用 `./memory`。

测试应断言文件位置、store 内容、清理结果和 scorer 输出等外部可观察状态，不以 Agent
最终文本声称成功作为证据。

## 文档迁移

实现完成时必须同步更新：

- `docs/file-edit-evaluation.md` 中关于 fixture `memory/` 自动读取和 eval trace 位置的描述，以及 `docs/tracing.md` 中 CLI 本地 trace 位置的描述；
- `docs/context-survival-benchmark/trace-input-contract.md` 中 `.eval_traces/` 的生成位置
  与 scorer 读取方式；
- eval runner、solver、trace reader 和 memory restart 相关测试说明。
