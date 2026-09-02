from audit_log import AuditLog
from deployment_repository import DeploymentRepository
from deployment_service import DeploymentService


def test_rollback_reason_survives_repository_reload() -> None:
    repository = DeploymentRepository(
        [{"deployment_id": "deploy-2", "status": "active"}]
    )
    service = DeploymentService(repository, AuditLog())

    service.rollback("deploy-2", "unsafe error rate")

    assert repository.get("deploy-2").rollback_reason == "unsafe error rate"


def test_repeated_rollback_is_idempotent_and_audited_once() -> None:
    repository = DeploymentRepository(
        [{"deployment_id": "deploy-3", "status": "active"}]
    )
    audit_log = AuditLog()
    service = DeploymentService(repository, audit_log)

    service.rollback("deploy-3", "first reason")
    service.rollback("deploy-3", "replacement reason")

    assert repository.get("deploy-3").rollback_reason == "first reason"
    assert [event.event_type for event in audit_log.events] == [
        "deployment_rolled_back"
    ]


def test_legacy_deployment_rows_remain_readable() -> None:
    repository = DeploymentRepository([{"deployment_id": "legacy", "status": "active"}])

    deployment = repository.get("legacy")

    assert deployment.deployment_id == "legacy"
    assert deployment.status == "active"
    assert deployment.rollback_reason is None
