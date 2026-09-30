"""murmur try: run a few real sessions, one at a time, and print every step.

It is for checking a graph against a real dev server before a swarm: each line shows
the request, its status and time, and the values it extracted, or why it failed.
"""

from __future__ import annotations

import os
import random
import time
from typing import Callable, TextIO

import requests

from .graph import EXIT, LoadGraph
from .http import send
from .pool import Pool
from .walker import PlaceholderError, Session, pick_persona

PATH_WIDTH = 60
VALUE_WIDTH = 24


def run_try(
    graph: LoadGraph,
    host: str,
    out: TextIO,
    *,
    sessions: int = 1,
    seed: int = 0,
    persona: str | None = None,
    pool: Pool | None = None,
    max_steps: int = 50,
    think: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the sessions and return how many steps failed."""
    rng = random.Random(seed)
    width = max(len(name) for name in graph.nodes)
    total = failed = 0
    for number in range(1, sessions + 1):
        chosen = graph.personas[persona] if persona else pick_persona(graph, rng)
        account = pool.lease(rng) if pool else None
        other = pool.other(rng, account) if pool else None
        accounts = {role: a.fields for role, a in (("user", account), ("other_user", other)) if a}
        who = f", account {account.index}" if account else (", no free pool account" if pool else "")
        print(f"session {number}: {chosen.name}{who}", file=out)

        session = Session(graph, chosen, rng, pool=accounts, env=os.environ)
        http = requests.Session()  # a fresh cookie jar for every session
        steps = session_failed = 0
        ended = "step limit"
        try:
            while steps < max_steps:
                name = session.next()
                if name == EXIT:
                    ended = "exit"
                    break
                steps += 1
                try:
                    step = session.prepare(name)
                except PlaceholderError as e:
                    session.fail(name)
                    session_failed += 1
                    print(f"  {steps:>3}  {name:<{width}}  failed before sending: {e}", file=out)
                    continue
                result = send(http, host, step, rng)
                session.complete(step, result.found)
                request = _shorten(f"{step.method} {step.path}", PATH_WIDTH) + (" (skip)" if step.via_skip else "")
                status = result.status if result.status is not None else "---"
                line = f"  {steps:>3}  {name:<{width}}  {request}  {status}  {result.elapsed_ms:.0f} ms"
                if result.error:
                    session_failed += 1
                    line += f"  failed: {result.error}"
                elif result.found:
                    line += "  " + " ".join(f"{k}={_shorten(str(v), VALUE_WIDTH)}" for k, v in result.found.items())
                print(line, file=out)
                if think:
                    sleep(think)
        finally:
            if account:
                pool.release(account)
        print(f"  ended by {ended} after {steps} steps, {session_failed} failed", file=out)
        total += steps
        failed += session_failed
    print(f"{sessions} sessions, {total} steps, {failed} failed", file=out)
    return failed


def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"
