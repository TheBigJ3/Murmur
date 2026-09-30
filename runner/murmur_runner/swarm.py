"""Build the Locust command for murmur swarm.

Locust runs in its own process with murmur_runner/locustfile.py, so importing it (which
patches Python's networking for gevent) never affects the murmur command itself.
"""

from __future__ import annotations

import os
import random
import sys
from collections import Counter
from pathlib import Path

import requests

from .graph import LoadGraph
from .http import Result, send
from .walker import PlaceholderError, Session

LOCUSTFILE = Path(__file__).with_name("locustfile.py")
WEB_HOST = "127.0.0.1"
REASON_WIDTH = 100


class Tally:
    """What happened in one Locust process, printed as the Murmur summary at the end."""

    def __init__(self):
        self.personas: Counter[str] = Counter()
        self.ended: Counter[str] = Counter()
        self.no_account = 0
        self.failures: Counter[tuple[str, str]] = Counter()
        self.empty: Counter[tuple[str, str]] = Counter()

    def failed(self, node: str, reason: str) -> None:
        self.failures[(node, reason[:REASON_WIDTH])] += 1

    def found_nothing(self, node: str, what: str) -> None:
        self.empty[(node, what[:REASON_WIDTH])] += 1

    def lines(self) -> list[str]:
        if not self.personas:
            return []
        mix = ", ".join(f"{name} {count}" for name, count in self.personas.most_common())
        running = sum(self.personas.values()) - sum(self.ended.values())
        lines = [
            f"Murmur: {sum(self.personas.values())} sessions ({mix})",
            f"Murmur: ended by exit {self.ended['exit']}, by the step limit {self.ended['step limit']}, "
            f"still running at the end {running}",
        ]
        if self.no_account:
            lines.append(f"Murmur: {self.no_account} sessions found no free pool account; add accounts to the pool")
        if self.empty:
            lines.append("Murmur: steps that found nothing to extract, so they set no flags (not failures)")
            for (node, what), count in self.empty.most_common(20):
                lines.append(f"  {node}: {what} ({count}x)")
        if self.failures:
            lines.append("Murmur: failed steps")
            for (node, reason), count in self.failures.most_common(20):
                lines.append(f"  {node}: {reason} ({count}x)")
        return lines


def warm_up(graph: LoadGraph, host: str, count: int, seed: int) -> list[Result] | str:
    """Send the start node's request count times, one after another, so a server or
    database that sleeps when idle is awake before the swarm. Returns the results, or
    why the start node could not be sent without an earlier step."""
    rng = random.Random(seed)
    persona = next(iter(graph.personas.values()))
    http = requests.Session()
    results = []
    for _ in range(count):
        session = Session(graph, persona, rng, env=os.environ)
        try:
            step = session.prepare(graph.start)
        except PlaceholderError as e:
            return str(e)
        results.append(send(http, host, step, rng))
    return results


def parse_think(text: str) -> tuple[float, float]:
    """MIN-MAX seconds, or one number for a fixed think time."""
    low, _, high = text.partition("-")
    try:
        low_s, high_s = float(low), float(high or low)
    except ValueError:
        raise ValueError(f"think time {text!r} must be MIN-MAX seconds, such as 1-5") from None
    if low_s < 0 or high_s < low_s:
        raise ValueError(f"think time {text!r} must be MIN-MAX seconds with 0 <= MIN <= MAX")
    return low_s, high_s


def parse_shard(text: str) -> tuple[int, int]:
    index, _, count = text.partition("/")
    try:
        i, n = int(index), int(count)
    except ValueError:
        raise ValueError(f"pool shard {text!r} must be K/N, such as 1/3") from None
    if not 1 <= i <= n:
        raise ValueError(f"pool shard {text!r} must have 1 <= K <= N")
    return i, n


def locust_command(
    graph_path: str,
    host: str,
    *,
    pool_path: str | None,
    pool_shard: str | None,
    users: int,
    spawn_rate: float,
    run_time: str | None,
    think: str,
    seed: int,
    max_steps: int,
    web: bool,
    extra: list[str],
    web_port: int = 8089,
    reset_stats: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """The argv and environment that run Locust for this swarm. With web, the run starts
    at once and Locust's dashboard shows it live, on this machine only; the dashboard
    stays up after the run until Ctrl+C."""
    argv = [
        sys.executable, "-m", "locust", "-f", str(LOCUSTFILE),
        "--host", host, "--users", str(users), "--spawn-rate", str(spawn_rate),
    ]
    if run_time:
        argv += ["--run-time", run_time]
    if web:
        argv += ["--autostart", "--web-host", WEB_HOST, "--web-port", str(web_port)]
    else:
        argv += ["--headless", "--only-summary"]
    if reset_stats:
        argv += ["--reset-stats"]
    argv += extra
    env = dict(os.environ)
    env.update(
        MURMUR_GRAPH=str(Path(graph_path).resolve()),
        MURMUR_THINK=think,
        MURMUR_SEED=str(seed),
        MURMUR_MAX_STEPS=str(max_steps),
    )
    if pool_path:
        env["MURMUR_POOL"] = str(Path(pool_path).resolve())
    if pool_shard:
        env["MURMUR_POOL_SHARD"] = pool_shard
    return argv, env
