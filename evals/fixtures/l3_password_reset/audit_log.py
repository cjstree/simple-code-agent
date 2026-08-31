from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEvent:
    action: str
    user_id: str


class AuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record_password_reset(self, user_id: str) -> None:
        self._events.append(AuditEvent("password_reset", user_id))

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)
