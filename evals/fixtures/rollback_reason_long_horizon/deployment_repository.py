from collections.abc import Iterable

from deployment_models import Deployment


class DeploymentRepository:
    def __init__(self, rows: Iterable[dict[str, str]] = ()) -> None:
        self._rows = {row["deployment_id"]: dict(row) for row in rows}

    def save(self, deployment: Deployment) -> None:
        self._rows[deployment.deployment_id] = {
            "deployment_id": deployment.deployment_id,
            "status": deployment.status,
        }

    def get(self, deployment_id: str) -> Deployment:
        row = self._rows[deployment_id]
        return Deployment(
            deployment_id=row["deployment_id"],
            status=row["status"],
        )
