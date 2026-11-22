"""SQLite storage for K11 Shop."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

PRICES = {"BAG-1": 42.50, "HAT-2": 15.00, "MUG-3": 9.90}

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (sku TEXT PRIMARY KEY, price REAL NOT NULL, stock INTEGER NOT NULL,
                                      reserved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, sku TEXT NOT NULL,
                                   qty INTEGER NOT NULL, total REAL NOT NULL, channel TEXT NOT NULL,
                                   status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sku_totals (sku TEXT PRIMARY KEY, qty INTEGER NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        with sqlite3.connect(self.path) as c:
            c.executescript(SCHEMA)
            c.executemany("INSERT OR IGNORE INTO inventory (sku, price, stock, reserved) VALUES (?, ?, 1000, 0)",
                          PRICES.items())

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock, sqlite3.connect(self.path) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(sql, params)]

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.lock, sqlite3.connect(self.path) as c:
            return c.execute(sql, params).rowcount

    def transaction(self, fn):
        """Run fn(connection) atomically."""
        with self.lock, sqlite3.connect(self.path) as c:
            return fn(c)
