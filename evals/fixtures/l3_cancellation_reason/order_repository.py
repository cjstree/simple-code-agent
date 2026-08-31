from order_models import Order


class OrderRepository:
    def __init__(self, records: dict[str, dict[str, str]] | None = None) -> None:
        self._records = records if records is not None else {}

    def save(self, order: Order) -> None:
        self._records[order.order_id] = {
            "order_id": order.order_id,
            "status": order.status,
        }

    def get(self, order_id: str) -> Order | None:
        record = self._records.get(order_id)
        if record is None:
            return None
        return Order(order_id=record["order_id"], status=record["status"])
