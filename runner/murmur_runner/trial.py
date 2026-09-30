"""murmur try: run a few real sessions, one at a time, and print every step.

It is for checking a graph against a real dev server before a swarm: each line shows
the request, its status and time, and the values it extracted, what it found nothing
for, or why it failed.
"""

from __future__ import annotations

import os
import random
import time
from typing import Callable, TextIO

import requests

from .actor import Actor
from .board import Board
from .graph import EXIT, LoadGraph
from .http import send
from .pool import Pool
from .walker import PlaceholderError

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
    grow: bool = True,
    max_steps: int = 50,
    think: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the sessions and return how many steps failed. A step that found nothing to
    extract is reported but does not count as failed."""
    rng = random.Random(seed)
    actor = Actor(graph, rng, pool=pool, board=Board(), env=os.environ, persona=persona, grow=grow)
    width = max(len(name) for name in graph.nodes)
    total = failed = empty = 0
    for number in range(1, sessions + 1):
        session = actor.begin()
        who = f", account {actor.account.index}" if actor.account else (", no account yet" if pool else "")
        print(f"session {number}: {session.persona.name}{who}", file=out)
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
                else:
                    if result.found:
                        line += "  " + " ".join(f"{k}={_shorten(str(v), VALUE_WIDTH)}" for k, v in result.found.items())
                    if result.empty:
                        empty += 1
                        line += f"  {result.empty}, so its sets and clears were skipped"
                print(line, file=out)
                settled = actor.settle()
                for account in settled.new:
                    print(f"       {account.describe()} joined the pool", file=out)
                for account, groups in settled.joined:
                    print(f"       account {account.index} joined {', '.join(groups)}", file=out)
                for problem in session.problems:
                    print(f"       note: {problem}", file=out)
                session.problems.clear()
                if think:
                    sleep(think)
        finally:
            actor.end()
        print(f"  ended by {ended} after {steps} steps, {session_failed} failed", file=out)
        total += steps
        failed += session_failed
    print(f"{sessions} sessions, {total} steps, {failed} failed, {empty} found nothing", file=out)
    return failed


def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"
