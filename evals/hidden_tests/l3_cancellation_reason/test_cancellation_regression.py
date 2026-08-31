from order_api import cancel_order
from order_models import Order
from order_repository import OrderRepository
from order_service import OrderService


def test_cancellation_reason_survives_repository_reload() -> None:
    records: dict[str, dict[str, str]] = {}
    repository = OrderRepository(records)
    repository.save(Order(order_id="order-7", status="open"))

    cancel_order(
        {"order_id": "order-7", "reason": "placed by mistake"},
        OrderService(repository),
    )

    reloaded = OrderRepository(records).get("order-7")
    assert reloaded is not None
    assert reloaded.cancellation_reason == "placed by mistake"


def test_existing_order_records_still_load_and_report_their_status() -> None:
    repository = OrderRepository(
        {"legacy-1": {"order_id": "legacy-1", "status": "open"}}
    )

    order = repository.get("legacy-1")

    assert order is not None
    assert order.order_id == "legacy-1"
    assert order.status == "open"
