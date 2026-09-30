"""A small API for the shop graph in conftest.py, for testing real requests.

Login returns its token in the Authorization response header and sets a refresh cookie.
Signup with an email and password creates an account that can log in. Cart and checkout
need the token, refresh needs the cookie, and the Murmur endpoints need X-Murmur-Key.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

KEY = "key-123"
ACCOUNTS = {"pool1@test.com": "hunter22", "pool2@test.com": "hunter33"}


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeAPI"
    accounts: dict[str, str] = {}  # each server gets its own copy, see start()
    rate_limits = "on"

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, status, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else None

    def _authed(self):
        return (self.headers.get("Authorization") or "").startswith("Bearer tok-")

    def _murmur(self):
        return self.headers.get("X-Murmur-Key") == KEY

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/internal/murmur/health":
            if not self._murmur():
                return self._send(404, {"error": "not found"})
            return self._send(200, {"murmur": "ok", "rate_limits": self.rate_limits})
        if path == "/":
            return self._send(200, {"ok": True})
        if path == "/products":
            return self._send(200, {"items": [{"id": "p1"}, {"id": "p2"}]})
        if path == "/refresh":
            if "refresh=r1" not in (self.headers.get("Cookie") or ""):
                return self._send(401, {"error": "no refresh cookie"})
            return self._send(200, {"ok": True}, {"Authorization": "Bearer tok-refreshed", "Set-Cookie": "refresh=r1; Path=/"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body() or {}
        if path == "/login":
            if self.accounts.get(body.get("email")) != body.get("password"):
                return self._send(401, {"error": "bad credentials"})
            return self._send(
                200, {"ok": True},
                {"Authorization": f"Bearer tok-{body['email']}", "Set-Cookie": "refresh=r1; Path=/"},
            )
        if path == "/signup":
            if body.get("email") and body.get("password"):
                self.accounts[body["email"]] = body["password"]
            return self._send(200, {"id": f"u{len(self.accounts)}"})
        if path.startswith("/cart/"):
            return self._send(200, {"cart": [path.rsplit("/", 1)[1]]}) if self._authed() else self._send(401, {"error": "unauthorized"})
        if path == "/internal/murmur/complete-order":
            if not self._murmur():
                return self._send(404, {"error": "not found"})
            return self._send(200, {"order": {"id": "o1"}}) if self._authed() else self._send(401, {"error": "unauthorized"})
        if path == "/logout":
            return self._send(204)
        return self._send(404, {"error": "not found"})


def start(rate_limits: str = "on") -> tuple[ThreadingHTTPServer, str]:
    handler = type("Handler", (Handler,), {"accounts": dict(ACCOUNTS), "rate_limits": rate_limits})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
