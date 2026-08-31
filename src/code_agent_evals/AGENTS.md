# Evaluation implementation instructions

## Component responsibilities

- `Solver` translates a dataset sample into a sandboxed runner invocation and
  transports the runner result back to the evaluation framework.
- `Runner` validates the payload, configures and runs the Agent, owns its
  lifecycle, and keeps protocol output separate from diagnostics.
- `Scorer` determines correctness from externally observable outcomes. Keep
  task execution out of the scorer except for explicit verification commands.

## Scoring and failure classification

- A scorer must inspect evidence such as files, exit codes, emitted events, or
  test results. Never award success solely because the Agent says it succeeded
  or because its final text matches a success phrase.
- Treat a completed evaluation that fails its expected outcome as an
  evaluation failure. Treat an unusable sandbox, missing dependency, runner
  protocol error, model outage, or other inability to execute the evaluation
  as an infrastructure failure. Preserve diagnostics and do not convert
  infrastructure failures into ordinary score-zero results.

## Reproducibility

- Evaluation runs must be replayable from the fixture, dataset record, runner
  payload, and recorded configuration. Keep inputs immutable and isolate each
  run in a fresh workspace.
- Fix model identity/version, random seeds, clocks, and other nondeterministic
  inputs where the boundary supports it. Otherwise record their exact values,
  together with relevant timeouts and runtime configuration, in run metadata.
- Keep protocol data deterministic and machine-readable; send incidental logs
  to diagnostics rather than mixing them into scored output.
