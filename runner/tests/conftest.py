import copy
import json

import pytest

# A small shop: browse and search, log in, add to cart, and check out through a
# skip in place of Stripe. It is valid and produces no warnings; each test breaks
# one thing in a copy of it.
VALID_GRAPH = {
    "murmur_version": "0.2.0",
    "start": "home",
    "session_flags": ["authed", "has_cart"],
    "test_rules": {
        "test_email": {
            "description": "emails on @test.com are auto-verified",
            "generate": "{rand}@test.com",
            "source": "src/auth.ts:isTestEmail",
            "dev_only": True,
        },
        "test_otp": {
            "description": "test phones accept a fixed OTP",
            "value": "000000",
            "source": "src/otp.ts:verify",
            "dev_only": True,
        },
    },
    "headers": {"Authorization": "{token}"},
    "nodes": {
        "home": {"request": "GET /"},
        "search": {
            "request": "GET /products?q={gen:query}",
            "extract": {"productId": {"path": "$.items[*].id", "pick": "random", "required": True}},
            "sets": ["has_results"],
        },
        "login": {
            "request": "POST /login",
            "body": {"email": "{pool:user.email}", "password": "{pool:user.password}"},
            "extract": {"token": {"header": "Authorization", "required": True}},
            "requires_not": ["authed"],
            "sets": ["authed"],
        },
        "signup": {
            "request": "POST /signup",
            "body": {"email": "{test:test_email}", "otp": "{test:test_otp}"},
            "requires_not": ["authed"],
            "sets": ["authed"],
        },
        "add_to_cart": {
            "request": "POST /cart/{productId}",
            "requires": ["authed", "has_results"],
            "sets": ["has_cart"],
        },
        "checkout": {
            "request": "POST /checkout",
            "requires": ["has_cart"],
            "skip": {
                "reason": "Stripe",
                "request": "POST /internal/murmur/complete-order",
                "headers": {"X-Murmur-Key": "{env:MURMUR_KEY}"},
                "extract": {"orderId": {"path": "$.order.id", "pick": "first", "required": True}},
                "clears": ["has_cart"],
            },
        },
        "logout": {"request": "POST /logout", "requires": ["authed"], "clears": ["@session"]},
    },
    "edges": {
        "home": [
            {"to": "search", "p": 0.5, "tag": "browse"},
            {"to": "login", "p": 0.2, "tag": "account"},
            {"to": "signup", "p": 0.1, "tag": "account"},
            {"to": "exit", "p": 0.2, "tag": "exit"},
        ],
        "search": [
            {"to": "search", "p": 0.2, "tag": "browse"},
            {"to": "add_to_cart", "p": 0.3, "tag": "purchase"},
            {"to": "home", "p": 0.3, "tag": "browse"},
            {"to": "exit", "p": 0.2, "tag": "exit"},
        ],
        "login": [
            {"to": "search", "p": 0.6, "tag": "browse"},
            {"to": "logout", "p": 0.1, "tag": "account"},
            {"to": "exit", "p": 0.3, "tag": "exit"},
        ],
        "signup": [
            {"to": "search", "p": 0.7, "tag": "browse"},
            {"to": "exit", "p": 0.3, "tag": "exit"},
        ],
        "add_to_cart": [
            {"to": "checkout", "p": 0.5, "tag": "purchase"},
            {"to": "search", "p": 0.3, "tag": "browse"},
            {"to": "exit", "p": 0.2, "tag": "exit"},
        ],
        "checkout": [
            {"to": "home", "p": 0.5, "tag": "browse"},
            {"to": "logout", "p": 0.2, "tag": "account"},
            {"to": "exit", "p": 0.3, "tag": "exit"},
        ],
        "logout": [{"to": "exit", "p": 1, "tag": "exit"}],
    },
    "personas": {
        "browser": {"share": 0.6, "multipliers": {"browse": 1.5}},
        "buyer": {"share": 0.4, "multipliers": {"purchase": 2, "exit": 0.5}},
    },
}


@pytest.fixture
def graph() -> dict:
    """A fresh copy of the valid graph, safe to change."""
    return copy.deepcopy(VALID_GRAPH)


@pytest.fixture
def write_graph(tmp_path):
    """Writes a graph dict to a file and returns its path."""

    def write(data: dict, name: str = "loadgraph.json"):
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return write


POOL_ACCOUNTS = {
    "accounts": [
        {"email": "pool1@test.com", "password": "hunter22"},
        {"email": "pool2@test.com", "password": "hunter33"},
    ]
}


@pytest.fixture
def api():
    """A running fake API for the graph above; yields its base URL."""
    from fake_api import start

    server, url = start()
    yield url
    server.shutdown()
    server.server_close()


@pytest.fixture
def pool_file(tmp_path):
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(POOL_ACCOUNTS), encoding="utf-8")
    return path


@pytest.fixture
def murmur_key(monkeypatch):
    monkeypatch.setenv("MURMUR_KEY", "key-123")
