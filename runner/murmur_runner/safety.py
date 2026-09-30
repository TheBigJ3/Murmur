"""Checks before Murmur sends any traffic.

Every mapped project has GET /internal/murmur/health, registered only in dev builds and
protected by X-Murmur-Key like the skip endpoints. A 200 from it means the target runs in
dev mode with the same key, so its test rules are on and its skips work. Anything else
means steps could reach real SMS, email or payment providers, so Murmur refuses to run.
"""

from __future__ import annotations

import ipaddress
from typing import Mapping
from urllib.parse import urlparse

import requests

HEALTH_PATH = "/internal/murmur/health"
KEY_ENV = "MURMUR_KEY"
KEY_HEADER = "X-Murmur-Key"


class SafetyError(Exception):
    pass


def check_host(host: str) -> str:
    """The host with no trailing slash, or SafetyError when it is not an http(s) URL."""
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SafetyError(f"--host must be an http:// or https:// URL, not {host!r}")
    return host.rstrip("/")


def is_local(host: str) -> bool:
    name = urlparse(host).hostname or ""
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


RATE_LIMITS_RELAXED = "relaxed"


def preflight(host: str, env: Mapping[str, str], timeout: float = 10.0) -> dict:
    """Raise SafetyError unless the host answers the Murmur health check. Returns the
    health response, which says whether the target's rate limits are relaxed."""
    key = env.get(KEY_ENV, "")
    if not key:
        raise SafetyError(f"{KEY_ENV} is not set; set it to the key the target's dev build uses")
    url = host + HEALTH_PATH
    try:
        response = requests.get(url, headers={KEY_HEADER: key}, timeout=timeout)
    except requests.RequestException:
        raise SafetyError(f"GET {url} failed: could not reach {host}") from None
    if response.status_code == 404:
        raise SafetyError(
            f"GET {url} returned 404: the target is not in dev mode, has no Murmur health "
            f"endpoint yet (run murmur-start again), or uses a different {KEY_ENV}. "
            "Refusing to run, because steps could reach real SMS, email or payment providers."
        )
    if response.status_code != 200:
        raise SafetyError(f"GET {url} returned {response.status_code}, not 200. Refusing to run.")
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
