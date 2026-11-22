"""HTTP API for K11 Shop. Every route except /health needs a bearer token; customers only see their own orders."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from ..auth.tokens import authenticate
from ..bus import EVENTS

CHANNELS = {"web", "mobile", "pos"}
ORDER = re.compile(r"^/orders/([A-Za-z0-9-]{1,64})$")
CANCEL = re.compile(r"^/orders/([A-Za-z0-9-]{1,64})/cancel$")
MAX_BODY = 64 * 1024


def public(o: dict) -> dict:
    """The documented order representation; nothing else leaves the service."""
    return {"id": o["id"], "customerId": o["customer_id"], "sku": o["sku"], "qty": o["qty"],
            "total": o["total"], "channel": o["channel"], "status": o["status"], "createdAt": o["created_at"]}


class Orders:
    def __init__(self, store, bus, users: dict[str, str]):
        self.store, self.bus, self.users = store, bus, users

    def create(self, customer: str, body: dict) -> tuple[int, dict]:
        sku, qty, channel = body.get("sku"), body.get("qty"), body.get("channel", "web")
        if not isinstance(sku, str) or not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= 100:
            return 400, {"error": "sku (string) and qty (integer 1-100) are required"}
        if channel not in CHANNELS:
            return 400, {"error": f"channel must be one of {sorted(CHANNELS)}"}
        price = self.store.query("SELECT price FROM inventory WHERE sku = ?", (sku,))
        if not price:
            return 400, {"error": f"unknown sku {sku}"}
        order = {"id": str(uuid.uuid4()), "customer_id": customer, "sku": sku, "qty": qty,
                 "total": round(price[0]["price"] * qty, 2), "channel": channel, "status": "pending",
                 "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.store.execute("INSERT INTO orders (id, customer_id, sku, qty, total, channel, status, created_at) "
                           "VALUES (:id, :customer_id, :sku, :qty, :total, :channel, :status, :created_at)", order)
        self.bus.publish(EVENTS, {"type": "OrderCreated", "eventId": str(uuid.uuid4()), "orderId": order["id"],
                                  "customerId": customer, "sku": sku, "qty": qty, "total": order["total"],
                                  "at": order["created_at"]}, key=order["id"], headers={"source": "orders-api"})
        return 201, public(order)

    def get(self, customer: str, order_id: str) -> tuple[int, dict]:
        rows = self.store.query("SELECT * FROM orders WHERE id = ? AND customer_id = ?", (order_id, customer))
        return (200, public(rows[0])) if rows else (404, {"error": "order not found"})

    def list(self, customer: str) -> tuple[int, list]:
        rows = self.store.query("SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC, id", (customer,))
        return 200, [public(r) for r in rows]

    def cancel(self, order_id: str, customer: str | None = None) -> tuple[int, dict]:
        """Cancel a pending or reserved order. customer=None: a back-office command (any order)."""
        def tx(c):  # read and update together, so the inventory consumer can't reserve in between
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM orders WHERE id = ?" + (" AND customer_id = ?" if customer else ""),
                            (order_id, customer) if customer else (order_id,)).fetchone()
            if row is None or row["status"] not in ("pending", "reserved"):
                return dict(row) if row else None, False
            c.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            return dict(row), True

        o, changed = self.store.transaction(tx)
        if o is None:
            return 404, {"error": "order not found"}
        if not changed:
            return 409, {"error": f"an order that is {o['status']} can't be cancelled"}
        if o["status"] == "reserved":
            self.bus.publish(EVENTS, {"type": "OrderCancelled", "eventId": str(uuid.uuid4()), "orderId": order_id,
                                      "sku": o["sku"], "qty": o["qty"]}, key=order_id)
        return 200, public({**o, "status": "cancelled"})


def handler_for(orders: Orders):
    class Handler(BaseHTTPRequestHandler):
        server_version = "k11-shop"
        sys_version = ""

        def log_message(self, *args):
            pass

        def _send(self, code: int, body=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _customer(self) -> str | None:
            customer = authenticate(self.headers.get("Authorization"), orders.users)
            if customer is None:
                self._send(401, {"error": "missing or invalid bearer token"})
            return customer

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("request too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            return body

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/health":
                return self._send(200, {"status": "ok"})
            if path != "/orders" and not ORDER.match(path):
                return self._send(404, {"error": "not found"})
            customer = self._customer()
            if customer is None:
                return
            if path == "/orders":
                return self._send(*orders.list(customer))
            return self._send(*orders.get(customer, ORDER.match(path).group(1)))

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/orders" and not CANCEL.match(path):
                return self._send(404, {"error": "not found"})
            customer = self._customer()
            if customer is None:
                return
            if path != "/orders":
                return self._send(*orders.cancel(CANCEL.match(path).group(1), customer))
            try:
                body = self._json_body()
            except ValueError:
                return self._send(400, {"error": "malformed JSON body"})
            return self._send(*orders.create(customer, body))

    return Handler
