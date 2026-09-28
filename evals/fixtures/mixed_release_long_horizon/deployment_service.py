from audit_log import AuditLog
from deployment_models import Deployment
from deployment_repository import DeploymentRepository


class DeploymentService:
    def __init__(
        self,
        repository: DeploymentRepository,
        audit_log: AuditLog,
    ) -> None:
        self.repository = repository
        self.audit_log = audit_log

    def rollback(self, deployment_id: str, reason: str) -> Deployment:
        deployment = self.repository.get(deployment_id)
        deployment.status = "rolled_back"
        self.repository.save(deployment)
        self.audit_log.record("deployment_rolled_back", deployment_id)
        return deployment
