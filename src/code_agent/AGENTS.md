# Core agent instructions

## Responsibility boundaries

- The `Agent` loop owns turn orchestration: invoke the model, dispatch tool
  calls, run hooks, and coordinate component lifecycles. Do not move component
  storage or execution policy into the loop.
- `Session` is the source of truth for ordered, model-visible conversation
  state and compaction. Context construction and mutation must go through the
  Session contract.
- A `Tool` implements one operation and reports its outcome. Tool discovery,
  allow/block policy, approval, and dispatch belong to `ToolRegistry` and the
  surrounding policy path.
- `Memory` selects and persists durable cross-turn knowledge. It is not a
  transcript and must not replace Session history.

## Lifecycle and background work

- Every started task, subprocess, client, or async context must have one clear
  owner and an idempotent cleanup path.
- On cancellation, timeout, startup failure, or normal shutdown, stop accepting
  new work, cancel queued and running work as appropriate, terminate child
  process groups, await or reap them, and close external clients.
- Test cleanup after partial startup and cancellation, plus races between
  completion, collection, scheduling, and shutdown. Tests must not leave tasks
  or subprocesses running.

## Tool results and model-visible state

- All tool execution must pass through `ToolRegistry` and its policy/approval
  checks. A Tool or caller must not invoke another Tool directly to bypass that
  path.
- Convert a tool-call failure into the corresponding ordered `tool` result in
  Session, preserving its tool-call ID and an actionable failure description.
  Do not misreport failure as success or lose it only to terminal output.
- Propagate initialization and lifecycle failures that prevent a valid turn;
  do not disguise them as ordinary tool results.
- Decide explicitly whether newly model-visible content is transient or must
  survive later turns or compaction. Content that must remain visible belongs
  in Session (or durable Memory when it is genuinely cross-turn knowledge);
  terminal previews and telemetry alone are not persistence.

## Public contract changes

- For changes to Agent, Session, Tool/ToolRegistry, Memory, hooks, streaming, or
  lifecycle APIs, add focused unit coverage for the changed component and an
  integration test across each affected boundary.
- Cover success, failure, cancellation/cleanup, ordering, and persistence as
  applicable. Update CLI or evaluation tests when their public interaction with
  the core contract changes.
