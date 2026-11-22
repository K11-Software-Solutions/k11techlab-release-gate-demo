"""Unit tests for K11 Shop (in-process bus, temporary database)."""

import json
import time
import urllib.error
import urllib.request

import pytest

from shop.app import Shop
from shop.bus import COMMANDS, DLQ

ALICE = {"Authorization": "Bearer alice-test-token"}
BOB = {"Authorization": "Bearer bob-test-token"}


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOP_TEST_TOKENS", raising=False)
    s = Shop(0, tmp_path / "shop.db")
    yield s
    s.close()


def call(shop, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(shop.url + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def eventually(fn, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return fn()


def test_order_total_and_reservation(shop):
    status, order = call(shop, "POST", "/orders", {"sku": "BAG-1", "qty": 2, "channel": "mobile"}, ALICE)
    assert status == 201 and order["total"] == 85.0 and order["status"] == "pending" and order["customerId"] == "u-alice"
    assert eventually(lambda: call(shop, "GET", f"/orders/{order['id']}", headers=ALICE)[1]["status"] == "reserved")
    assert shop.store.query("SELECT reserved FROM inventory WHERE sku = 'BAG-1'")[0]["reserved"] == 2


def test_order_response_has_only_documented_fields(shop):
    _, order = call(shop, "POST", "/orders", {"sku": "HAT-2", "qty": 1}, ALICE)
    assert set(order) == {"id", "customerId", "sku", "qty", "total", "channel", "status"}


def test_client_cannot_set_server_fields(shop):
    _, order = call(shop, "POST", "/orders", {"sku": "HAT-2", "qty": 1, "total": 0.01, "status": "reserved",
                                              "customerId": "u-bob"}, ALICE)
    assert order["total"] == 15.0 and order["status"] == "pending" and order["customerId"] == "u-alice"


@pytest.mark.parametrize("body", [{"sku": "BAG-1"}, {"sku": "BAG-1", "qty": "2"}, {"sku": "BAG-1", "qty": 0},
                                  {"sku": "NOPE", "qty": 1}, {"sku": "BAG-1", "qty": 1, "channel": "fax"}])
def test_invalid_orders_are_rejected(shop, body):
    assert call(shop, "POST", "/orders", body, ALICE)[0] == 400


def test_malformed_json_is_a_clean_400(shop):
    status, body = call(shop, "POST", "/orders", raw=b'{"sku": [unclosed', headers=ALICE)
    assert status == 400 and "Traceback" not in json.dumps(body)


def test_authentication_is_required(shop):
    assert call(shop, "GET", "/orders")[0] == 401
    assert call(shop, "GET", "/orders", headers={"Authorization": "Bearer wrong"})[0] == 401
    assert call(shop, "GET", "/health")[0] == 200


def test_customers_only_see_their_own_orders(shop):
    _, order = call(shop, "POST", "/orders", {"sku": "BAG-1", "qty": 1}, ALICE)
    assert call(shop, "GET", f"/orders/{order['id']}", headers=BOB)[0] == 404
    assert call(shop, "POST", f"/orders/{order['id']}/cancel", headers=BOB)[0] == 404
    assert call(shop, "GET", "/orders", headers=BOB)[1] == []


def test_cancel_releases_stock(shop):
    _, order = call(shop, "POST", "/orders", {"sku": "MUG-3", "qty": 4}, ALICE)
    eventually(lambda: call(shop, "GET", f"/orders/{order['id']}", headers=ALICE)[1]["status"] == "reserved")
    status, cancelled = call(shop, "POST", f"/orders/{order['id']}/cancel", headers=ALICE)
    assert status == 200 and cancelled["status"] == "cancelled"
    assert eventually(lambda: shop.store.query("SELECT reserved FROM inventory WHERE sku = 'MUG-3'")[0]["reserved"] == 0)
    assert call(shop, "POST", f"/orders/{order['id']}/cancel", headers=ALICE)[0] == 409


def test_unreadable_command_is_dead_lettered(shop):
    shop.bus.publish(COMMANDS, "cancel order 42 please", key="k1")
    assert eventually(lambda: any(t == DLQ and k == "k1" and "error" in h for t, _, k, h in shop.bus.published))
