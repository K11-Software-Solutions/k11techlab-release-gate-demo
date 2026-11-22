"""K11 Shop: an orders API with Kafka events, an inventory consumer, a dead-letter queue and a batch job.

    python -m shop.app --port 8095 --db shop.db --kafka localhost:9092
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from .api.handlers import Orders, handler_for
from .auth.tokens import users
from .bus import COMMANDS, EVENTS, InProcessBus, K11MemoryBus, KafkaBus
from .consumers.commands import Commands
from .consumers.inventory import Inventory
from .db import Store


class Shop:
    def __init__(self, port: int = 0, db: Path | str = "shop.db", bus=None):
        self.store = Store(Path(db))
        self.bus = bus or InProcessBus()
        self.orders = Orders(self.store, self.bus, users())
        self.inventory = Inventory(self.store, self.bus)
        self.commands = Commands(self.store, self.bus, lambda oid: self.orders.cancel(oid))
        self.bus.consume(EVENTS, "inventory", self.inventory.on_event)
        self.bus.consume(COMMANDS, "commands", self.commands.on_command)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_for(self.orders))
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.bus.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="K11 Shop")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SHOP_PORT", 8095)))
    ap.add_argument("--db", default=os.environ.get("SHOP_DB", "shop.db"))
    ap.add_argument("--kafka", default=os.environ.get("KAFKA_BOOTSTRAP"), help="bootstrap servers (default: in-process bus)")
    a = ap.parse_args()
    shop = Shop(a.port, a.db, KafkaBus(a.kafka) if a.kafka else None)
    print(f"K11 Shop on {shop.url} (messaging: {'kafka ' + a.kafka if a.kafka else 'in-process'})", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        shop.close()


if __name__ == "__main__":
    main()
