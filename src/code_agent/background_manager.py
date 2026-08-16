"""Background command scheduling for the local Bash tool."""

import asyncio


class BackgroundManager:
    """Schedule and collect background shell commands for one agent.

    The manager is bound to the running asyncio event loop on which ``start`` is
    called. Commands wait in submission order, and no more than ``max_process``
    commands may run at once. Finished results remain available until a caller
    consumes them with ``collect``.
    """

    MAX_OUTPUT_CHARS = 100_000
    TRUNCATION_MARKER = "\n...(output truncated)"

    ready: dict[str, str]
    running: set[str]
    finish: dict[str, str]
    max_process: int
    time_out: float
    terminate_grace_period: float
    _workers: set[asyncio.Task[None]]
    _closed: bool

    def __init__(
        self,
        max_process: int = 3,
        time_out: float = 30,
        terminate_grace_period: float = 2,
    ) -> None:
        """Configure limits and initialize independent state for this manager.

        All numeric limits must be positive. Each manager owns its ready,
        running, finished-result, and worker collections. Task IDs need only be
        unique during the lifetime of one manager.
        """

    def start(self, cmd: str) -> str:
        """Register ``cmd``, try to schedule work, and return its ID immediately.

        This method must be called from the manager's running event loop. It
        adds the command to ``ready`` and calls ``_schedule`` but never waits for
        the command to start or finish. Calling it after ``close`` raises
        ``RuntimeError``.
        """

    def collect(self) -> list[tuple[str, str]]:
        """Return and consume all finished results in completion order.

        Collection never inspects or waits for running processes and does not
        drive scheduling. If no command has finished, it returns an empty list
        immediately.
        """

    def _schedule(self) -> None:
        """Fill available process slots from ``ready`` without waiting.

        For each available slot, move the command ID into ``running`` and use
        ``asyncio.create_task`` to schedule ``_execute`` on the current event
        loop. Keep every created task in ``_workers`` until its done callback
        removes it, so fire-and-forget workers retain a strong reference.
        """

    async def _execute(self, task_id: str, cmd: str) -> None:
        """Run and supervise one command, then publish its formatted result.

        Start the shell in a new POSIX session, merge stderr into stdout, and
        continuously drain output while retaining at most ``MAX_OUTPUT_CHARS``.
        The timeout starts when execution starts, not while the command waits in
        ``ready``. On timeout or cancellation, terminate the whole process group
        with SIGTERM, escalate to SIGKILL after ``terminate_grace_period``, and
        reap the root process.

        Success, non-zero exit, timeout, and process-start errors are formatted
        with ``_format_res`` and stored in ``finish``. Cancellation caused by
        ``close`` performs cleanup but publishes no result. In all cases remove
        the ID from ``running``; after normal completion call ``_schedule``
        immediately unless the manager is closed.
        """

    def _format_res(
        self,
        output: str,
        *,
        returncode: int | None = None,
        timed_out: bool = False,
        timeout: float | None = None,
        error: BaseException | None = None,
    ) -> str:
        """Format a completed command outcome as compact LLM-readable text."""
        if error is not None:
            status = f"[failed to start: {type(error).__name__}: {error}]"
        elif timed_out:
            duration = self.time_out if timeout is None else timeout
            status = f"[timed out after {duration:g}s]"
        elif returncode == 0:
            status = "[succeeded]"
        elif returncode is None:
            status = "[failed: exit code unavailable]"
        else:
            status = f"[failed: exit code {returncode}]"

        rendered_output = output.strip() or "(empty)"
        if len(rendered_output) > self.MAX_OUTPUT_CHARS:
            rendered_output = (
                rendered_output[: self.MAX_OUTPUT_CHARS] + self.TRUNCATION_MARKER
            )
        return f"{status}\n{rendered_output}"

    async def close(self) -> None:
        """Stop all work and release subprocess resources.

        Mark the manager closed, discard queued and finished entries, cancel and
        await all workers, and rely on each worker to terminate and reap its own
        process group. The method is idempotent and schedules no replacement
        work while shutdown is in progress.
        """
