"""Messaging: Apache Kafka in CI and production-like runs, or an in-process bus for unit tests and local demos."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

EVENTS, COMMANDS, DLQ = "orders.events", "orders.commands", "orders.dlq"
Handler = Callable[[str, "str | None", dict], None]  # (raw value, key, headers)


def _bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    return (value if isinstance(value, str) else json.dumps(value)).encode("utf-8")


class InProcessBus:
    """Delivers each message to subscribers on a thread, like a consumer would."""

    def __init__(self):
        self.subscribers: dict[str, list[Handler]] = {}
        self.published: list[tuple[str, Any, str | None, dict]] = []

    def publish(self, topic: str, value: Any, key: str | None = None, headers: dict | None = None) -> None:
        self.published.append((topic, value, key, dict(headers or {})))
        raw = _bytes(value).decode("utf-8")
        for h in self.subscribers.get(topic, []):
            threading.Thread(target=h, args=(raw, key, dict(headers or {})), daemon=True).start()

    def consume(self, topic: str, group: str, handler: Handler) -> None:
        self.subscribers.setdefault(topic, []).append(handler)

    def close(self) -> None:
        pass


class K11MemoryBus(InProcessBus):
    """Shares k11 Integration QA's in-process broker, so its flows can run against the shop in one process."""

    def __init__(self, name: str = "shop"):
        super().__init__()
        from k11_integration_qa.brokers import MemoryBroker

        self.broker = MemoryBroker.named(name)

    def publish(self, topic, value, key=None, headers=None):
        self.broker.publish(topic, value, key, headers)

    def consume(self, topic, group, handler):
        self.broker.subscribe(topic, lambda m: handler(m.raw, m.key, m.headers))


class KafkaBus:
    def __init__(self, bootstrap: str):
        from kafka import KafkaConsumer, KafkaProducer

        self.bootstrap, self._consumer_cls = bootstrap, KafkaConsumer
        self.producer = KafkaProducer(bootstrap_servers=bootstrap)
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def publish(self, topic: str, value: Any, key: str | None = None, headers: dict | None = None) -> None:
        self.producer.send(topic, value=_bytes(value), key=key.encode() if key else None,
                           headers=[(k, str(v).encode()) for k, v in (headers or {}).items()]).get(timeout=15)

    def consume(self, topic: str, group: str, handler: Handler) -> None:
        consumer = self._consumer_cls(topic, bootstrap_servers=self.bootstrap, group_id=f"shop-{group}",
                                      auto_offset_reset="latest", enable_auto_commit=True)
        ready = threading.Event()

        def run():
            while not self.stop.is_set():
                batch = consumer.poll(timeout_ms=200)
                if consumer.assignment():
                    ready.set()
                for records in batch.values():
                    for r in records:
                        hdrs = {k: v.decode("utf-8", errors="replace") for k, v in (r.headers or [])}
                        handler(r.value.decode("utf-8", errors="replace"), r.key.decode() if r.key else None, hdrs)
            consumer.close()

        t = threading.Thread(target=run, daemon=True, name=f"consumer-{group}")
        t.start()
        self.threads.append(t)
        ready.wait(30)  # partitions assigned: from here on no message is missed

    def close(self) -> None:
        self.stop.set()
        for t in self.threads:
            t.join(timeout=3)
        self.producer.close(timeout=3)
