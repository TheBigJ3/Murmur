"""The murmur command."""

from __future__ import annotations

import argparse
import os
import random
import signal
import subprocess
import sys
from pathlib import Path

from . import __version__
from .graph import GraphError, LoadGraph, Problem, load_graph
from .pool import (
    DEFAULT_POOL, Pool, PoolError, check_pool, drop_incomplete_grown, grown_files, grown_name,
    growable_groups, pool_notes,
)
from .safety import RATE_LIMITS_RELAXED, SafetyError, check_host, is_local, preflight
from .simulate import format_report, simulate
from .swarm import locust_command, parse_shard, parse_think, warm_up
from .trial import run_try

DEFAULT_GRAPH = ".murmur/loadgraph.json"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Everything after -- goes to Locust unchanged, for murmur swarm.
    extra: list[str] = []
    if "--" in argv:
        extra = argv[argv.index("--") + 1 :]
        argv = argv[: argv.index("--")]

    parser = argparse.ArgumentParser(prog="murmur", description="Murmur load testing runner.")
    parser.add_argument("--version", action="version", version=f"murmur {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="check a load graph and report every problem")
    _graph_argument(validate)

    sim = commands.add_parser("simulate", help="walk simulated sessions through a load graph without sending requests")
    _graph_argument(sim)
    sim.add_argument("--sessions", type=_positive, default=1000, help="sessions to walk (default: 1000)")
    sim.add_argument("--seed", type=int, help="random seed, to repeat a run (default: a new one, printed)")
    sim.add_argument("--persona", help="run every session as this persona")
    sim.add_argument("--max-steps", type=_positive, default=500, help="end a session after this many steps (default: 500)")
    sim.add_argument("--show", type=int, default=5, help="example sessions to print (default: 5)")
    sim.add_argument(
        "--users", type=_positive, default=100,
        help="the swarm size to weigh fixed-count personas against (default: 100)",
    )

    trial = commands.add_parser("try", help="run a few real sessions one at a time and print every step")
    _graph_argument(trial)
    _target_arguments(trial)
    trial.add_argument("--sessions", type=_positive, default=1, help="sessions to run (default: 1)")
    trial.add_argument("--seed", type=int, help="random seed (default: a new one, printed)")
    trial.add_argument("--persona", help="run every session as this persona")
    trial.add_argument("--max-steps", type=_positive, default=50, help="end a session after this many steps (default: 50)")
    trial.add_argument("--think", type=float, default=0.0, help="seconds to wait between steps (default: 0)")

    swarm = commands.add_parser(
        "swarm", help="run many simulated users at once with Locust; options after -- go to Locust"
    )
    _graph_argument(swarm)
    _target_arguments(swarm)
    swarm.add_argument("--users", type=_positive, default=10, help="simulated users at once (default: 10)")
    swarm.add_argument("--spawn-rate", type=float, default=1.0, help="users started per second (default: 1)")
    swarm.add_argument("--run-time", default="1m", help="how long to run, such as 30s, 10m or 1h (default: 1m)")
    swarm.add_argument("--think", default="1-5", help="seconds between a user's steps, MIN-MAX (default: 1-5)")
    swarm.add_argument("--pool-shard", help="use only share K of N of the pool, when N machines run workers")
    swarm.add_argument("--seed", type=int, help="random seed (default: a new one, printed)")
    swarm.add_argument("--max-steps", type=_positive, default=200, help="end a session after this many steps (default: 200)")
    swarm.add_argument(
        "--warm-up", type=int, nargs="?", const=3, default=0, metavar="N",
        help="send the start node N times first (3 when N is left out), and leave the ramp-up out of the statistics",
    )
    swarm.add_argument("--web", action="store_true", help="show the run live in Locust's dashboard on this machine")
    swarm.add_argument("--web-port", type=_positive, default=8089, help="the dashboard's port (default: 8089)")

    args = parser.parse_args(argv)
    if extra and args.command != "swarm":
        parser.error("only murmur swarm takes options after --")
    if args.command == "simulate":
        return _simulate(args)
    if args.command == "try":
        return _try(args)
    if args.command == "swarm":
        return _swarm(args, extra)
    return _validate(args.path)


def _graph_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=DEFAULT_GRAPH, help=f"the load graph (default: {DEFAULT_GRAPH})")


def _target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="the dev server to send requests to, such as http://localhost:3000")
    parser.add_argument("--pool", help=f"the test account pool (default: {DEFAULT_POOL} when it exists)")
    parser.add_argument(
        "--no-grow", action="store_true",
        help=f"don't add accounts that steps create to the pool or save them to {grown_name()}",
    )
    parser.add_argument("--yes", action="store_true", help="allow a host that is not on this machine")
    parser.add_argument(
        "--no-preflight", action="store_true",
        help="skip the dev mode health check; steps may then reach real SMS, email or payment providers",
    )


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def _fail(message: str) -> int:
    print(f"murmur: {message}", file=sys.stderr)
    return 1


def _load(path: str) -> LoadGraph | None:
    try:
        return load_graph(path)
    except GraphError as e:
        _report(e.source, e.errors, e.warnings)
        return None


def _prepare_run(
    args: argparse.Namespace, shard: tuple[int, int] | None = None
) -> tuple[LoadGraph, Pool | None, str, str | None, str | None] | int:
    """Every check before real traffic: the graph, the pool (this machine's shard of it),
    the host, and the preflight. Returns the graph, the pool, the host, the pool file and
    the grown accounts file."""
    graph = _load(args.path)
    if graph is None:
        return 1
    if getattr(args, "persona", None) and args.persona not in graph.personas:
        return _fail(f"no persona '{args.persona}' (have: {', '.join(graph.personas)})")
    try:
        host = check_host(args.host)
    except SafetyError as e:
        return _fail(str(e))
    pool_path = args.pool or (DEFAULT_POOL if Path(DEFAULT_POOL).exists() else None)
    folder = Path(pool_path or DEFAULT_POOL).parent
    grown_path = str(folder / grown_name(shard))
    others = [str(f) for f in grown_files(folder) if f.name != Path(grown_path).name]
    try:
        if pool_path or Path(grown_path).exists() or others or growable_groups(graph):
            pool = Pool.load(pool_path, grown_path, others)
            if shard:
                pool = pool.shard(*shard)
        else:
            pool = None
    except PoolError as e:
        return _fail(str(e))
    for note in drop_incomplete_grown(graph, pool):
        print(f"murmur: note: {note}", file=sys.stderr)
    if pool is not None and args.no_grow:
        pool.grown_path = None  # read the saved accounts, but save no new ones
    problems = check_pool(graph, pool)
    if problems:
        return _fail("the pool cannot serve this graph:\n" + "\n".join(f"  {p}" for p in problems))
    for note in pool_notes(graph, pool, grow=not args.no_grow):
        print(f"murmur: note: {note}", file=sys.stderr)
    if not is_local(host) and not args.yes:
        return _fail(f"{host} is not on this machine. Pass --yes if it is a dev server you mean to load.")
    if args.no_preflight:
        print("murmur: warning: skipping the health check; steps may reach real providers", file=sys.stderr)
    else:
        try:
            health = preflight(host, os.environ)
        except SafetyError as e:
            return _fail(str(e))
        if health.get("rate_limits") != RATE_LIMITS_RELAXED:
            print(
                "murmur: note: the target's rate limits are on, so results show its limits as well as "
                "its performance. murmur-start can add a dev-only switch that relaxes them.",
                file=sys.stderr,
            )
    return graph, pool, host, pool_path, grown_path


def _try(args: argparse.Namespace) -> int:
    prepared = _prepare_run(args)
    if isinstance(prepared, int):
        return prepared
    graph, pool, host, _, _ = prepared
    seed = args.seed if args.seed is not None else random.randrange(2**32)
    print(f"murmur try: {host}, seed {seed}")
    failed = run_try(
        graph, host, sys.stdout, sessions=args.sessions, seed=seed, persona=args.persona,
        pool=pool, grow=not args.no_grow, max_steps=args.max_steps, think=args.think,
    )
    return 1 if failed else 0


def _swarm(args: argparse.Namespace, extra: list[str]) -> int:
    try:
        parse_think(args.think)
        shard = parse_shard(args.pool_shard) if args.pool_shard else None
    except ValueError as e:
        return _fail(str(e))
    prepared = _prepare_run(args, shard=shard)
    if isinstance(prepared, int):
        return prepared
    if prepared[1] is not None and any(a == "--processes" or a.startswith("--processes=") for a in extra):
        return _fail(
            "Locust's --processes gives every process its own copy of the pool, so they would "
            "lease the same accounts. Run one murmur swarm --worker per process with its own "
            "--pool-shard K/N instead."
        )
    graph, pool, host, pool_path, grown_path = prepared
    grows = not args.no_grow and bool(growable_groups(graph))
    if pool is not None and args.users > len(pool.accounts) and not grows:
        print(
            f"murmur: note: {args.users} users share {len(pool.accounts)} pool accounts, so some "
            "sessions will run without one and fail the steps that need it",
            file=sys.stderr,
        )
    fixed = sum(p.count or 0 for p in graph.personas.values())
    if fixed and args.users < fixed:
        print(
            f"murmur: note: personas with a fixed count need {fixed} users, so with --users {args.users} "
            "some get fewer than their count and none are left for the personas with a share",
            file=sys.stderr,
        )
    elif fixed and args.users == fixed:
        print(
            f"murmur: note: personas with a fixed count take all {fixed} users, so none are left for "
            "the personas with a share",
            file=sys.stderr,
        )
    if args.warm_up < 0:
        return _fail("--warm-up must be 0 or more")
    seed = args.seed if args.seed is not None else random.randrange(2**32)
    hint = "" if args.web else " (add --web to watch it live)"
    print(f"murmur swarm: {host}, {args.users} users, seed {seed}{hint}", flush=True)
    if args.warm_up:
        _print_warm_up(graph, host, args.warm_up, seed)
    command, env = locust_command(
        args.path, host, pool_path=pool_path, pool_shard=args.pool_shard,
        grown_path=grown_path if pool is not None else None, grow=not args.no_grow, users=args.users,
        grown_others=[str(f) for f in grown_files(Path(grown_path).parent) if str(f) != grown_path],
        spawn_rate=args.spawn_rate, run_time=args.run_time, think=args.think, seed=seed,
        max_steps=args.max_steps, web=args.web, extra=extra, web_port=args.web_port,
        reset_stats=bool(args.warm_up),
    )
    if args.web:
        print(
            f"murmur: live dashboard at http://localhost:{args.web_port} "
            "(the run starts now and the dashboard stays open afterwards; Ctrl+C to stop)",
            flush=True,
        )
    return _run_locust(command, env)


def _run_locust(command: list[str], env: dict[str, str]) -> int:
    """Run Locust and wait for it. Ctrl+C in a terminal reaches Locust as well, which then
    stops and prints its statistics and the Murmur summary, so murmur waits for that
    instead of killing it. If Locust is still running shortly after, it did not get the
    Ctrl+C, so murmur passes it on once."""
    process = subprocess.Popen(command, env=env)
    forwarded = False
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            if forwarded:
                continue
            try:
                return process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.send_signal(signal.SIGINT)
                forwarded = True
            except KeyboardInterrupt:
                continue


def _print_warm_up(graph: LoadGraph, host: str, count: int, seed: int) -> None:
    results = warm_up(graph, host, count, seed)
    if isinstance(results, str):
        print(f"murmur: warm-up skipped: the start node {graph.start} needs an earlier step ({results})", flush=True)
        return
    timings = ", ".join(
        f"{r.status if r.status is not None else r.error} in {r.elapsed_ms:.0f} ms" for r in results
    )
    print(f"murmur: warm-up, {count} x {graph.start}: {timings}", flush=True)


def _simulate(args: argparse.Namespace) -> int:
    graph = _load(args.path)
    if graph is None:
        return 1
    if args.persona and args.persona not in graph.personas:
        print(f"{args.path}: no persona '{args.persona}' (have: {', '.join(graph.personas)})", file=sys.stderr)
        return 1
    seed = args.seed if args.seed is not None else random.randrange(2**32)
    report = simulate(graph, args.sessions, seed, persona=args.persona, max_steps=args.max_steps, users=args.users)
    print(format_report(graph, report, args.path, show=args.show))
    return 0


def _validate(path: str) -> int:
    graph = _load(path)
    if graph is None:
        return 1
    edges = sum(len(out) for out in graph.edges.values())
    print(
        f"{path}: valid, {_count(len(graph.nodes), 'node')}, {_count(edges, 'edge')}, "
        f"{_count(len(graph.personas), 'persona')}, {_count(len(graph.warnings), 'warning')}"
    )
    _print_problems((), graph.warnings)
    return 0


def _report(source: str, errors: tuple[Problem, ...], warnings: tuple[Problem, ...]) -> None:
    print(f"{source}: {_count(len(errors), 'error')}, {_count(len(warnings), 'warning')}", file=sys.stderr)
    _print_problems(errors, warnings, file=sys.stderr)


def _print_problems(errors: tuple[Problem, ...], warnings: tuple[Problem, ...], file=None) -> None:
    for problem in errors:
        print(f"  error    {problem}", file=file)
    for problem in warnings:
        print(f"  warning  {problem}", file=file)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
