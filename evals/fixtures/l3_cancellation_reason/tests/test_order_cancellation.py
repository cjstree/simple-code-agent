from order_api import cancel_order
from order_models import Order
from order_repository import OrderRepository
from order_service import OrderService


def test_cancellation_response_contains_the_supplied_reason() -> None:
    repository = OrderRepository()
    repository.save(Order(order_id="order-1", status="open"))

    response = cancel_order(
        {"order_id": "order-1", "reason": "duplicate"},
        OrderService(repository),
    )

    assert response == {
        "order_id": "order-1",
        "status": "cancelled",
        "cancellation_reason": "duplicate",
    }


def test_cancellation_still_changes_order_status() -> None:
    repository = OrderRepository()
    repository.save(Order(order_id="order-1", status="open"))

    response = cancel_order(
        {"order_id": "order-1", "reason": "duplicate"},
        OrderService(repository),
    )

    assert response["status"] == "cancelled"
