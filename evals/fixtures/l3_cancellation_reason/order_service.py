from order_models import Order
from order_repository import OrderRepository


class OrderService:
    def __init__(self, repository: OrderRepository) -> None:
        self._repository = repository

    def cancel(self, order_id: str) -> Order:
        order = self._repository.get(order_id)
        if order is None:
            raise KeyError(order_id)
        cancelled = Order(order_id=order.order_id, status="cancelled")
        self._repository.save(cancelled)
        return cancelled
