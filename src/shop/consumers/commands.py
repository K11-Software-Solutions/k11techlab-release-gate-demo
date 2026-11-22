"""Commands consumer: `orders.commands` carries {"action": "cancel", "orderId": ...} from back-office tools.

A message it can't read goes to the dead-letter queue `orders.dlq` with an "error" header, so nothing is lost silently.
"""

from __future__ import annotations

import json

from ..bus import DLQ
from ..db import Store


class Commands:
    def __init__(self, store: Store, bus, cancel):
        self.store, self.bus, self.cancel = store, bus, cancel  # cancel(order_id) -> bool

    def on_command(self, raw: str, key: str | None, headers: dict) -> None:
        try:
            cmd = json.loads(raw)
            if not isinstance(cmd, dict) or cmd.get("action") != "cancel" or not cmd.get("orderId"):
                raise ValueError('expected {"action": "cancel", "orderId": ...}')
        except ValueError as e:
            self.bus.publish(DLQ, raw, key=key, headers={**headers, "error": str(e)[:120] or "not JSON"})
            return
        self.cancel(str(cmd["orderId"]))
