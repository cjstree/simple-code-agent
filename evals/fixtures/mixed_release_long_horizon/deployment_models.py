from dataclasses import dataclass


@dataclass
class Deployment:
    deployment_id: str
    status: str = "active"
    rollback_reason: str | None = None
