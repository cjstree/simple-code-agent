import inspect

from audit_log import AuditLog
from deployment_api import rollback_deployment
from deployment_repository import DeploymentRepository
from deployment_service import DeploymentService


def test_rollback_signature_stays_compatible() -> None:
    # The service's required public arguments keep their names, kinds, and types.
    signature = inspect.signature(DeploymentService.rollback)
    assert list(signature.parameters) == ["self", "deployment_id", "reason"]
    for name in ("deployment_id", "reason"):
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameter.default is inspect.Parameter.empty
        assert parameter.annotation is str


def test_legacy_row_remains_readable_after_new_rollback() -> None:
    # Saving a new rollback does not make a row without rollback_reason unreadable.
    repository = DeploymentRepository(
        [
            {"deployment_id": "legacy", "status": "active"},
            {"deployment_id": "new", "status": "active"},
        ]
    )
    service = DeploymentService(repository, AuditLog())

    service.rollback("new", "operator request")

    assert repository.get("legacy").rollback_reason is None
    assert repository.get("new").rollback_reason == "operator request"


def test_rollback_preserves_unrelated_stored_fields() -> None:
    # Persisting a reason leaves an existing row's unrelated fields intact.
    repository = DeploymentRepository(
        [{"deployment_id": "tagged", "status": "active", "owner": "release-team"}]
    )
    service = DeploymentService(repository, AuditLog())

    service.rollback("tagged", "failed health check")

    assert repository.export_row("tagged") == {
        "deployment_id": "tagged",
        "status": "rolled_back",
        "owner": "release-team",
        "rollback_reason": "failed health check",
    }


def test_repeated_rollback_keeps_first_reason_and_one_audit_event() -> None:
    # Retrying rollback does not overwrite the original reason or record again.
    repository = DeploymentRepository(
        [{"deployment_id": "deploy-3", "status": "active"}]
    )
    audit_log = AuditLog()
    service = DeploymentService(repository, audit_log)

    first = rollback_deployment(
        service, {"deployment_id": "deploy-3", "reason": "first reason"}
    )
    second = rollback_deployment(
        service, {"deployment_id": "deploy-3", "reason": "replacement reason"}
    )

    assert first["rollback_reason"] == "first reason"
    assert second["rollback_reason"] == "first reason"
    assert repository.get("deploy-3").rollback_reason == "first reason"
    assert [event.event_type for event in audit_log.events] == [
        "deployment_rolled_back"
    ]
