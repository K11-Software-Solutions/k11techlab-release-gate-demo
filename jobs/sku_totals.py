"""Nightly batch job: total reserved quantity per SKU into sku_totals.

Recomputes the totals from the orders table, so running it twice (a retry, or a rerun after a failure) is safe.

    SHOP_DB=shop.db python jobs/sku_totals.py
"""

import os
import sqlite3

db = os.environ.get("SHOP_DB", "shop.db")
with sqlite3.connect(db) as c:
    rows = c.execute("SELECT sku, SUM(qty) FROM orders WHERE status = 'reserved' GROUP BY sku").fetchall()
    c.execute("DELETE FROM sku_totals")
    c.executemany("INSERT INTO sku_totals (sku, qty) VALUES (?, ?)", rows)
print(f"sku_totals: {len(rows)} sku(s)")
