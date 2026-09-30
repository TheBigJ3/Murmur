"""Send a prepared step over HTTP and judge the response.

judge is shared by murmur try, which sends with requests, and murmur swarm, which sends
through Locust's client. A step has one of three outcomes:

- It failed: the status is not 2xx, or the request never got a response.
- It found nothing: the status is 2xx, but a required extract matched nothing, such as
  a user with no orders yet. The request worked, so it is not a failure, but the step's
  own sets and clears are skipped, as the walker does for any step without its required
  values. Its extracts' unlocks and locks still follow the response.
- It succeeded with every required value.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Mapping

import requests

from .walker import Step, extract_values

EXCERPT_LENGTH = 160
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class Judgement:
    found: dict[str, Any] | None  # the extracted values; None when the request failed
    error: str | None  # why the request failed
    empty: str | None = None  # which required extracts found nothing, when the request worked


@dataclass(frozen=True)
class Result:
    status: int | None
    elapsed_ms: float
    found: dict[str, Any] | None
    error: str | None
    empty: str | None = None


def judge(step: Step, status: int, headers: Mapping[str, str], text: str, rng: random.Random) -> Judgement:
    if not 200 <= status < 300:
        return Judgement(None, f"HTTP {status}{_excerpt(text)}")
    try:
        body = json.loads(text) if text.strip() else None
    except ValueError:
        body = None
    found = extract_values(step, body, rng, headers)
    missing = [var for var, e in step.extracts.items() if e.required and var not in found]
    empty = None
    if missing:
        empty = f"{', '.join(missing)} found nothing"
        if body is None and any(step.extracts[var].path for var in missing):
            empty += " (the response has no JSON body)"
    return Judgement(found, None, empty)


def send(http: requests.Session, host: str, step: Step, rng: random.Random, timeout: float = DEFAULT_TIMEOUT) -> Result:
    started = time.perf_counter()
    try:
        response = http.request(
            step.method,
            host.rstrip("/") + step.path,
            headers=step.headers,
            json=step.body if step.body is not None else None,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return Result(None, (time.perf_counter() - started) * 1000, None, f"request failed: {_reason(e)}")
    elapsed = (time.perf_counter() - started) * 1000
    verdict = judge(step, response.status_code, response.headers, response.text, rng)
    return Result(response.status_code, elapsed, verdict.found, verdict.error, verdict.empty)


def _excerpt(text: str) -> str:
    flat = " ".join(text.split())
    if not flat:
        return ""
    return ": " + (flat if len(flat) <= EXCERPT_LENGTH else flat[: EXCERPT_LENGTH - 1] + "…")


def _reason(error: Exception) -> str:
    if isinstance(error, requests.ConnectionError):
        return "could not connect"
    if isinstance(error, requests.Timeout):
        return "timed out"
    return type(error).__name__
