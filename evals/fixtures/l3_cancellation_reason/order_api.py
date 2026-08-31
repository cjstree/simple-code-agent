from dataclasses import asdict

from order_service import OrderService


def cancel_order(payload: dict[str, str], service: OrderService) -> dict[str, str]:
    cancelled = service.cancel(payload["order_id"])
    return asdict(cancelled)
