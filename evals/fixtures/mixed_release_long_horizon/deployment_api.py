from deployment_service import DeploymentService


def rollback_deployment(
    service: DeploymentService,
    payload: dict[str, str],
) -> dict[str, str]:
    # Older callers send only deployment_id. Their reason is "unspecified".
    if "reason" in payload:
        deployment = service.rollback(payload["deployment_id"], payload["reason"])
    else:
        deployment = service.rollback(payload["deployment_id"])
    return {
        "deployment_id": deployment.deployment_id,
        "status": deployment.status,
    }
