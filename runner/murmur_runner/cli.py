"""The murmur command."""

from __future__ import annotations

import argparse
import sys

from .graph import GraphError, Problem, load_graph

DEFAULT_GRAPH = ".murmur/loadgraph.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="murmur", description="Murmur load testing runner.")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="check a load graph and report every problem")
    validate.add_argument("path", nargs="?", default=DEFAULT_GRAPH, help=f"the load graph (default: {DEFAULT_GRAPH})")
    args = parser.parse_args(argv)
    return _validate(args.path)


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
