from deployment_service import DeploymentService


def rollback_deployment(
    service: DeploymentService,
    payload: dict[str, str],
) -> dict[str, str]:
    deployment = service.rollback(
        payload["deployment_id"],
        payload["reason"],
    )
    return {
        "deployment_id": deployment.deployment_id,
        "status": deployment.status,
    }
