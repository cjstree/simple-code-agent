from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    deployment_id: str


class AuditLog:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event_type: str, deployment_id: str) -> None:
        self.events.append(AuditEvent(event_type, deployment_id))
