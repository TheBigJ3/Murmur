"""Build the Locust command for murmur swarm.

Locust runs in its own process with murmur_runner/locustfile.py, so importing it (which
patches Python's networking for gevent) never affects the murmur command itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

LOCUSTFILE = Path(__file__).with_name("locustfile.py")


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
) -> tuple[list[str], dict[str, str]]:
    """The argv and environment that run Locust for this swarm."""
    argv = [
        sys.executable, "-m", "locust", "-f", str(LOCUSTFILE),
        "--host", host, "--users", str(users), "--spawn-rate", str(spawn_rate),
    ]
    if run_time:
        argv += ["--run-time", run_time]
    if not web:
        argv += ["--headless", "--only-summary"]
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
