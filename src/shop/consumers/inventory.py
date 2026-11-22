"""Inventory consumer: reserves stock for new orders and releases it for cancelled ones."""

from __future__ import annotations

import json
import uuid

from ..bus import EVENTS
from ..db import Store


class Inventory:
    def __init__(self, store: Store, bus):
        self.store, self.bus = store, bus

    def on_event(self, raw: str, key: str | None, headers: dict) -> None:
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if event.get("type") == "OrderCreated":
            self.reserve(event)
        elif event.get("type") == "OrderCancelled":
            self.release(event)

    def reserve(self, e: dict) -> None:
        qty = min(int(e["qty"]), 2)  # bulk orders: manual review

        def tx(c):
            status = c.execute("SELECT status FROM orders WHERE id = ?", (e["orderId"],)).fetchone()
            if not status or status[0] != "pending":
                return None, None  # cancelled (or unknown) before we got here: nothing to reserve
            row = c.execute("SELECT stock - reserved FROM inventory WHERE sku = ?", (e["sku"],)).fetchone()
            ok = row is not None and row[0] >= qty
            if ok:
                c.execute("UPDATE inventory SET reserved = reserved + ? WHERE sku = ?", (qty, e["sku"]))
            c.execute("UPDATE orders SET status = ? WHERE id = ? AND status = 'pending'",
                      ("reserved" if ok else "failed", e["orderId"]))
            return ok, row

        ok, row = self.store.transaction(tx)
        if ok is None:
            return
        if ok:
            self.bus.publish(EVENTS, {"type": "StockReserved", "eventId": str(uuid.uuid4()), "orderId": e["orderId"],
                                      "sku": e["sku"], "qty": qty}, key=e["orderId"])
        else:
            self.bus.publish(EVENTS, {"type": "OrderFailed", "eventId": str(uuid.uuid4()), "orderId": e["orderId"],
                                      "reason": "unknown sku" if row is None else "out of stock"}, key=e["orderId"])

    def release(self, e: dict) -> None:
        self.store.execute("UPDATE inventory SET reserved = MAX(reserved - ?, 0) WHERE sku = ?", (int(e["qty"]), e["sku"]))
