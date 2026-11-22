"""Bearer-token authentication. The demo has two TEST users; their tokens come from the environment."""

from __future__ import annotations

import hmac
import os


def users() -> dict[str, str]:
    """token -> customer id, from SHOP_TEST_TOKENS="alice:<token>,bob:<token>" (demo defaults otherwise)."""
    raw = os.environ.get("SHOP_TEST_TOKENS", "alice:alice-test-token,bob:bob-test-token")
    out = {}
    for pair in raw.split(","):
        name, _, token = pair.strip().partition(":")
        if name and token:
            out[token] = f"u-{name}"
    return out


def authenticate(header: str | None, known: dict[str, str]) -> str | None:
    """The caller's customer id, or None. Constant-time comparison of the token."""
    if not header or not header.startswith("Bearer "):
        return None
    given = header[len("Bearer "):].strip()
    for token, customer in known.items():
        if hmac.compare_digest(given, token):
            return customer
    return None
