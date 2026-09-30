"""The murmur command."""

from __future__ import annotations

import argparse
import random
import sys

from . import __version__
from .graph import GraphError, Problem, load_graph
from .simulate import format_report, simulate

DEFAULT_GRAPH = ".murmur/loadgraph.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="murmur", description="Murmur load testing runner.")
    parser.add_argument("--version", action="version", version=f"murmur {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="check a load graph and report every problem")
    validate.add_argument("path", nargs="?", default=DEFAULT_GRAPH, help=f"the load graph (default: {DEFAULT_GRAPH})")
    sim = commands.add_parser(
        "simulate", help="walk simulated sessions through a load graph without sending requests"
    )
    sim.add_argument("path", nargs="?", default=DEFAULT_GRAPH, help=f"the load graph (default: {DEFAULT_GRAPH})")
    sim.add_argument("--sessions", type=_positive, default=1000, help="sessions to walk (default: 1000)")
    sim.add_argument("--seed", type=int, help="random seed, to repeat a run (default: a new one, printed)")
    sim.add_argument("--persona", help="run every session as this persona")
    sim.add_argument("--max-steps", type=_positive, default=500, help="end a session after this many steps (default: 500)")
    sim.add_argument("--show", type=int, default=5, help="example sessions to print (default: 5)")
    args = parser.parse_args(argv)
    if args.command == "simulate":
        return _simulate(args)
    return _validate(args.path)


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def _simulate(args: argparse.Namespace) -> int:
    try:
        graph = load_graph(args.path)
    except GraphError as e:
        _report(e.source, e.errors, e.warnings)
        return 1
    if args.persona and args.persona not in graph.personas:
        print(f"{args.path}: no persona '{args.persona}' (have: {', '.join(graph.personas)})", file=sys.stderr)
        return 1
    seed = args.seed if args.seed is not None else random.randrange(2**32)
    report = simulate(graph, args.sessions, seed, persona=args.persona, max_steps=args.max_steps)
    print(format_report(graph, report, args.path, show=args.show))
    return 0


def _validate(path: str) -> int:
    try:
        graph = load_graph(path)
    except GraphError as e:
        _report(e.source, e.errors, e.warnings)
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
