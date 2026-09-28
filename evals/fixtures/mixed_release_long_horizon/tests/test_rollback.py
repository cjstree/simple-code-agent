from audit_log import AuditLog
from deployment_api import rollback_deployment
from deployment_repository import DeploymentRepository
from deployment_service import DeploymentService


def make_service() -> tuple[DeploymentService, DeploymentRepository, AuditLog]:
    repository = DeploymentRepository(
        [{"deployment_id": "deploy-1", "status": "active"}]
    )
    audit_log = AuditLog()
    return DeploymentService(repository, audit_log), repository, audit_log


def test_rollback_response_contains_reason() -> None:
    # A supplied reason is returned after the rollback completes.
    service, _, _ = make_service()

    response = rollback_deployment(
        service,
        {"deployment_id": "deploy-1", "reason": "failed health check"},
    )

    assert response == {
        "deployment_id": "deploy-1",
        "status": "rolled_back",
        "rollback_reason": "failed health check",
    }


def test_rollback_still_changes_status() -> None:
    # A rollback continues to update the stored deployment status.
    service, repository, _ = make_service()

    service.rollback("deploy-1", "operator request")

    assert repository.get("deploy-1").status == "rolled_back"


def test_legacy_api_request_uses_unspecified_reason() -> None:
    # A caller using the old request shape receives a persisted fallback reason.
    service, repository, _ = make_service()

    response = rollback_deployment(service, {"deployment_id": "deploy-1"})

    assert response["rollback_reason"] == "unspecified"
    assert repository.get("deploy-1").rollback_reason == "unspecified"
