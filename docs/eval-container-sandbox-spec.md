# Eval 单样例容器隔离 Spec

## 状态与目标

本文是待实施的设计，不描述当前已上线的行为。当前两个 eval task 使用 Inspect
`local` sandbox；已实现的 trace/memory 目录隔离见
[`eval-runtime-state-isolation-spec.md`](eval-runtime-state-isolation-spec.md)。实施本方案时，
用 Inspect Docker sandbox 为每个 sample 创建一个容器，在容器内运行现有 eval runner、
Agent、Agent 的本地工具和测试命令。Inspect 的 solver/scorer 编排及数据集加载仍在宿主机。

目标：

- 每个 sample 有独立容器与工作目录，原始 fixture 不被修改；
- Agent 工作目录与该容器内的 eval trace/memory 临时根目录分离；
- 同一 sample 的多轮与 Agent restart 复用同一容器和 memory，sample 之间不共享状态；
- 现有 `TaskState.store` trace bundle、最终回答、visible/hidden pytest 评分语义不变；
- Agent 执行的文件工具和 shell 不直接访问宿主机文件系统或进程；
- CLI 与非 eval Agent 不受影响，不逐个改造本地工具。

非目标：抵抗容器内 Agent 主动读取其可见的 `/tmp`、进程环境或模型密钥；抵抗容器逃逸；
把 hidden tests、trace 或 memory 变成对容器内同一用户不可读的强安全域。
工作目录与临时目录分离只防止偶然发现，不是容器内部的权限边界。

## 进程与文件边界

```text
WSL / Inspect 宿主进程
  dataset、Task、solver、scorer、TaskState.store、Docker 控制
       │ sandbox().exec / read_file / write_file
       ▼
每个 sample 的 Inspect Docker 容器
  /workspace/                         fixture 副本与 Agent 当前工作目录
  /tmp/code-agent-eval-<random>/
    memory/                            从 /workspace/memory 移来的 fixture memory
    traces/                            manifest.jsonl 与 trace_log_*.jsonl
  runner → Agent → 本地工具 / bash / 后台进程
  scorer 请求在容器内执行 pytest
```

上图的 `/workspace` 是约定的容器工作目录，具体路径须在镜像/Compose 与 solver 中统一，
不能依赖当前 WSL 工作目录恰好在容器内同名。Inspect 将 sample `files` 复制进容器，
不把整个仓库、宿主 `.env`、用户主目录或 Docker socket 挂入容器。
宿主评分器仍读取本仓库中的 hidden test 源文件，Agent 结束后才通过
`sandbox().write_file()` 注入容器工作目录。

## 编排协议

1. Inspect 按 sample 创建容器，把 `files` 指定的 fixture 复制到容器工作目录。
   `basic_agent_eval` 与 `context_survival_eval` 使用同一 eval 镜像配置。
2. 宿主 solver 经 `sandbox().exec()` 在该容器内创建唯一的绝对临时根目录，获得其
   容器内路径；该路径由 solver 控制，不从 dataset metadata、prompt 或 Agent 输出取得。
3. 宿主 solver 经 `sandbox().exec()` 启动镜像内 Python 的
   `-m code_agent_evals.runner`，通过 stdin 传入现有 turns、配置、restart 轮次与
   临时路径。不能使用宿主 `sys.executable` 的绝对路径。stdout 仅传最终回答，
   stderr 传诊断；维持现有超时和基础设施错误分类。
4. runner 在第一个 Agent 启动前，于容器内准备 `traces/` 和 `memory/`，并将
   `/workspace/memory` 移入临时根目录。每次 Agent restart 继续使用同一 memory；
   Agent 结束且 telemetry flush 完成后，runner 才成功退出。
5. solver 在容器尚存活时，通过 `sandbox().read_file()` 读取 trace manifest 及其中
   引用的文件。读取前验证引用文件名为单层安全文件名，不接受绝对路径或 `..`；
   保持原有 trace 格式校验，再将 bundle 写入 `TaskState.store`。
   不把完整 trace 塞入 runner stdout：Inspect 对 `exec()` 输出有截断上限。
6. solver 在 `finally` 中清理容器内临时根目录；失败、超时、取消、trace 损坏均执行。
   清理失败不得掩盖原始错误，并记录诊断。Inspect 销毁 sample 容器是第二层回收，
   不能代替 solver 对运行时状态的清理责任。
7. 宿主 scorer 从 `TaskState.store` 解析 trace；Agent 已结束后，按当前顺序注入
   hidden tests，再通过 `sandbox().exec()` 在同一容器的工作目录运行 pytest。
   pytest 使用镜像内 Python；结果仍按现有 FAIL_TO_PASS、PASS_TO_PASS 与
   context-survival 契约评分。

容器内目录路径绝不作为宿主 `Path` 直接读取或删除。`TaskState.store` 是 trace 从容器
进入评分器的唯一持久协议；memory 不回传，随临时目录和容器销毁。

## 镜像、网络与资源

- 镜像固定 Python 版本、Agent 包、`pytest` 和样例运行所需依赖；记录镜像标识或
  digest、Agent 版本与模型配置，以便复现。镜像不得烘焙 `.env` 或 API key。
- Agent 目前由 runner 直接请求模型，所以容器必须能访问配置的模型 API；
  `network_mode: none` 会使这条路径失败。使用专门的 eval 网络配置，并只向 runner
  注入所需环境变量。网络可达不等于已限制出站目标；若需要严格出站控制，另设
  代理或网络策略。容器内 bash 与 Agent 同环境时仍可读取传入的密钥，这是本方案边界。
- `localhost` 在容器内指向容器自身。`LLM_BASE_URL`、Phoenix collector 与可选
  `MCP_URL` 若当前指向 WSL 或 Windows 的 localhost，实施时必须分别配置容器可达
  地址，或在 eval 中关闭非必需服务。
- 为容器设置非 root 用户、CPU/内存/PID 限额与 init；不使用 privileged 模式，
  不挂 Docker socket。限制并发 sample 数，避免同时运行多个 Agent/pytest 耗尽 WSL 内存。

## WSL 2 与 Docker Desktop 前提

项目位于 WSL 的 Linux 文件系统 `/home/cjs/agent`，符合 Docker 官方建议的开发位置。
截至本 spec 调查时，本机检查结果为：

| 检查 | 结果 |
| --- | --- |
| `wsl.exe --version` | WSL `2.7.14.0`，高于 Docker Desktop 的最低要求 `2.1.5` |
| `wsl.exe --list --verbose` | `Ubuntu-20.04` 与 `docker-desktop` 均在运行，版本均为 2 |
| `docker.exe version --format '{{.Server.Version}}'` | Docker Desktop Engine `29.7.2` 可连接 |
| `docker.exe compose version --short` | Compose `5.5.0` 可用 |
| `docker.exe info --format '{{.OSType}}'` | 当前 Engine 运行 Linux 容器模式 |
| WSL 内 `docker` 路径 | 当前 `/usr/bin/docker` 指向 Docker Desktop 注入的 Linux CLI |
| `/var/run/docker.sock` | 当前存在；在本会话执行沙箱内连接会被拒绝 |
| 沙箱外的 Linux `docker version --format '{{.Server.Version}}'` | 成功连接 Engine，返回 `29.7.2` |

因此路径在该 WSL 2 + Docker Desktop 组合上可行，Engine/Compose 版本也满足 Inspect
当前的最低要求；`Ubuntu-20.04` 的 Docker Desktop WSL integration 当前可用。
前一次检查曾命中 Windows Docker CLI 包装脚本并提示未集成，不能把该提示当作当前
发行版设置的持续状态。当前 Linux CLI 在本会话的受限执行沙箱内访问 Docker socket
会报 `permission denied`，但相同命令在沙箱外成功。这是执行环境权限差异，不能据此
判断 WSL integration 未启用。实际运行 eval 的 WSL 终端应检查：

```bash
docker version
docker compose version
docker info --format '{{.OSType}}'
```

预期是命令均成功，最后一项为 `linux`。Inspect 在 WSL Python 进程中运行，
需要相同运行环境能访问 Docker Engine。还需用一个最小 Inspect Docker sample
实测镜像构建、文件复制、容器内模型访问和 trace 回传，才能确认端到端可用。

## 实施与验收范围

实施集中在 eval task sandbox 配置、镜像/Compose、solver 的容器内路径与 trace 回传、
scorer 的容器内 Python 路径，以及对应测试和 README。现有 Agent 工具与生产 CLI
不应因本方案改为 Inspect 专用工具。

至少验证：

1. 两个 sample 并发时容器与临时根目录各自独立，原始 fixture 不变。
2. 工作目录中无 `memory/` 或 `.eval_traces/`，Agent restart 后能读取已持久化 memory。
3. Agent 本地读写和前台/后台 shell 均在容器内；尝试访问未挂载的宿主路径失败。
4. runner 成功后 trace 完整进入 `TaskState.store`，scorer 可解析并保持原评分结果。
5. hidden tests 只在 Agent 结束后注入，visible/hidden pytest 在容器内执行。
6. runner 失败、模型不可达、trace 缺失/损坏、超时和取消时，诊断与错误分类正确，
   临时目录及容器均被回收，不留下后台子进程。
7. 不运行 Docker 的本地单元测试路径与非 eval CLI 行为保持原样。

## 参考

- [Inspect Sandboxing](https://inspect.aisi.org.uk/sandboxing.html)：sample 级容器、
  `exec`/`read_file`/`write_file`、Docker 配置及输出上限。
- [Docker Desktop WSL 2 backend](https://docs.docker.com/desktop/features/wsl/)：
  WSL integration 设置与 Linux containers 模式。
- [Docker Desktop WSL 2 开发建议](https://docs.docker.com/desktop/features/wsl/use-wsl/)：
  将项目放在 WSL 的 Linux 文件系统中。
