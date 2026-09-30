"""Load and check a Murmur load graph, the .murmur/loadgraph.json that murmur-start writes.

load_graph checks the file in two passes. The first checks its structure against
loadgraph.schema.json. The second checks the rules that span several fields, such as
every node's edge probabilities summing to 1 and every flag a node requires being set
somewhere. Each pass reports every problem it finds, in one GraphError, so a mapping
can be fixed in one go. Problems that do not stop a graph from running are returned as
warnings on the LoadGraph.
"""

from __future__ import annotations

import json
import math
import re
from collections import deque
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterator, Literal

from jsonschema import Draft202012Validator

from .generators import GENERATORS

EXIT = "exit"
SESSION = "@session"
# Probabilities and persona shares must sum to 1 within this, which absorbs float
# rounding (0.1 + 0.2 + 0.7) but not a mistake (0.33 + 0.33 + 0.33).
TOLERANCE = 1e-6
SKIP_PREFIX = "/internal/murmur/"
KEY_HEADER = "X-Murmur-Key"
KEY_VALUE = "{env:MURMUR_KEY}"

# {kind:value} for test rules, the account pool, generated inputs and environment
# variables, or {name} for a value extracted by an earlier step.
PLACEHOLDER = re.compile(r"\{([a-z]+):([^{}\s]+)\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
KINDS = ("test", "pool", "gen", "env")

_PATTERN_MESSAGES = {
    "^[0-9]+\\.[0-9]+\\.[0-9]+$": "is not an X.Y.Z version",
    "^[A-Za-z0-9_.-]+$": "may only contain letters, digits, _, . and -",
    "^([A-Za-z0-9_.-]+|@session)$": "may only contain letters, digits, _, . and -, or be @session",
    "^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /\\S*$": "is not METHOD /path",
    "^\\$": "is not a JSONPath starting with $",
}


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a graph. location is a dotted path such as
    edges.home[2].to, or empty when the problem is with the whole file."""

    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}" if self.location else self.message


class GraphError(Exception):
    def __init__(self, source: str | Path, errors: list[Problem], warnings: list[Problem] = ()):
        self.source = str(source)
        self.errors = tuple(errors)
        self.warnings = tuple(warnings)
        super().__init__(f"{self.source}: " + "; ".join(str(e) for e in self.errors))


@dataclass(frozen=True)
class Request:
    method: str
    path: str


@dataclass(frozen=True)
class Extract:
    path: str
    pick: Literal["random", "first"]
    required: bool


@dataclass(frozen=True)
class Skip:
    reason: str
    request: Request
    headers: dict[str, str]
    body: Any
    extract: dict[str, Extract]
    sets: tuple[str, ...]
    clears: tuple[str, ...]


@dataclass(frozen=True)
class Node:
    name: str
    request: Request
    body: Any
    extract: dict[str, Extract]
    sets: tuple[str, ...]
    clears: tuple[str, ...]
    requires: tuple[str, ...]
    requires_not: tuple[str, ...]
    skip: Skip | None


@dataclass(frozen=True)
class Edge:
    to: str
    p: float
    tag: str


@dataclass(frozen=True)
class TestRule:
    __test__ = False  # not a pytest test class

    name: str
    description: str
    generate: str | None
    value: str | None
    source: str
    dev_only: bool


@dataclass(frozen=True)
class Persona:
    name: str
    share: float
    multipliers: dict[str, float]


@dataclass(frozen=True)
class LoadGraph:
    murmur_version: str
    start: str
    session_flags: tuple[str, ...]
    test_rules: dict[str, TestRule]
    nodes: dict[str, Node]
    edges: dict[str, tuple[Edge, ...]]
    personas: dict[str, Persona]
    warnings: tuple[Problem, ...]


def load_graph(path: str | Path) -> LoadGraph:
    """Read and check a load graph file. Raises GraphError listing every problem."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise GraphError(path, [Problem("", f"cannot read the file: {e.strerror}")]) from None
    return parse_graph(text, source=path)


def parse_graph(text: str, source: str | Path = "loadgraph.json") -> LoadGraph:
    """Check a load graph given as JSON text. Raises GraphError listing every problem."""
    duplicates: list[Problem] = []
    try:
        data = json.loads(
            text,
            object_pairs_hook=lambda pairs: _no_duplicates(pairs, duplicates),
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as e:
        raise GraphError(source, [Problem("", f"not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}")]) from None
    except ValueError as e:
        raise GraphError(source, [Problem("", f"not valid JSON: {e}")]) from None
    if duplicates:
        raise GraphError(source, duplicates)

    errors = _schema_errors(data)
    if errors:
        raise GraphError(source, errors)

    errors, warnings = _check(data)
    if errors:
        raise GraphError(source, errors, warnings)
    return _build(data, warnings)


def _no_duplicates(pairs: list[tuple[str, Any]], duplicates: list[Problem]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            duplicates.append(Problem("", f"duplicate key '{key}'"))
        seen[key] = value
    return seen


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not allowed")


@cache
def _schema() -> dict[str, Any]:
    text = resources.files("murmur_runner").joinpath("loadgraph.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def _location(parts: Any) -> str:
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _schema_errors(data: Any) -> list[Problem]:
    problems = []
    for error in Draft202012Validator(_schema()).iter_errors(data):
        message = error.message
        if error.validator == "pattern":
            message = f"{error.instance!r} {_PATTERN_MESSAGES.get(error.validator_value, message)}"
        elif error.validator == "not":
            message = f"{error.instance!r} is reserved for leaving the graph and cannot be a node name"
        elif error.validator == "oneOf":
            message = "a test rule needs exactly one of generate or value"
        problems.append(Problem(_location(error.absolute_path), message))
    return sorted(problems, key=lambda p: (p.location, p.message))


def _format_number(value: float) -> str:
    return f"{value:.10g}"


def _sums_to_one(values: list[float]) -> bool:
    return math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=TOLERANCE)


def _steps(name: str, node: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """The node itself and its skip, if any, each with its location."""
    yield f"nodes.{name}", node
    if "skip" in node:
        yield f"nodes.{name}.skip", node["skip"]


def _strings(value: Any) -> Iterator[str]:
    """Every string in a JSON value, including object keys."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)


def _successors(edges: dict[str, list[dict[str, Any]]], name: str) -> list[str]:
    """Nodes a session can move to from name. An edge with p = 0 is never taken."""
    return [e["to"] for e in edges.get(name, []) if e["to"] != EXIT and e["p"] > 0]


def _reachable(edges: dict[str, list[dict[str, Any]]], start: str, stop: set[str] = frozenset()) -> set[str]:
    """Nodes reachable from start, not continuing past any node in stop."""
    seen = {start}
    queue = deque([start])
    while queue:
        name = queue.popleft()
        if name in stop:
            continue
        for nxt in _successors(edges, name):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _check(data: dict[str, Any]) -> tuple[list[Problem], list[Problem]]:
    errors: list[Problem] = []
    warnings: list[Problem] = []
    nodes = data["nodes"]
    edges = data["edges"]
    personas = data["personas"]
    rules = data["test_rules"]

    if data["start"] not in nodes:
        errors.append(Problem("start", f"'{data['start']}' is not a node"))

    for name in nodes:
        if name not in edges:
            errors.append(Problem(f"edges.{name}", "the node has no outgoing edges"))

    for name, out in edges.items():
        loc = f"edges.{name}"
        if name not in nodes:
            errors.append(Problem(loc, f"'{name}' is not a node"))
            continue
        if not _sums_to_one([e["p"] for e in out]):
            total = _format_number(math.fsum(e["p"] for e in out))
            errors.append(Problem(loc, f"probabilities sum to {total}, not 1"))
        if not any(e["to"] == EXIT for e in out):
            errors.append(Problem(loc, "no edge to exit"))
        for i, edge in enumerate(out):
            if edge["to"] != EXIT and edge["to"] not in nodes:
                errors.append(Problem(f"{loc}[{i}].to", f"'{edge['to']}' is not a node"))
            if edge["to"] == EXIT and edge["tag"] != EXIT:
                errors.append(Problem(f"{loc}[{i}].tag", "an edge to exit must have the tag exit"))
            if edge["to"] != EXIT and edge["tag"] == EXIT:
                errors.append(Problem(f"{loc}[{i}].tag", "the tag exit is only for edges to exit"))

    if not _sums_to_one([p["share"] for p in personas.values()]):
        total = _format_number(math.fsum(p["share"] for p in personas.values()))
        errors.append(Problem("personas", f"shares sum to {total}, not 1"))
    tags = {e["tag"] for out in edges.values() for e in out}
    for pname, persona in personas.items():
        for tag in persona.get("multipliers", {}):
            if tag not in tags:
                errors.append(Problem(f"personas.{pname}.multipliers.{tag}", f"no edge has the tag '{tag}'"))

    flags_set = {f for name, node in nodes.items() for _, step in _steps(name, node) for f in step.get("sets", [])}

    def check_flags(loc: str, flags: list[str]) -> None:
        for flag in flags:
            if flag != SESSION and flag not in flags_set:
                errors.append(Problem(loc, f"flag '{flag}' is never set"))

    check_flags("session_flags", data["session_flags"])
    uses: list[tuple[str, str, str]] = []  # (node, variable, location)
    extractors: dict[str, set[str]] = {}
    for name, node in nodes.items():
        check_flags(f"nodes.{name}.requires", node.get("requires", []))
        check_flags(f"nodes.{name}.requires_not", node.get("requires_not", []))
        for loc, step in _steps(name, node):
            check_flags(f"{loc}.clears", step.get("clears", []))
            for var in step.get("extract", {}):
                extractors.setdefault(var, set()).add(name)
            # Placeholders can sit in the request, headers and body, and inside an
            # extract's JSONPath, such as a filter on a value extracted earlier.
            texts = [(f"{loc}.{field}", _strings(step.get(field))) for field in ("request", "headers", "body")]
            texts += [(f"{loc}.extract.{var}.path", [e["path"]]) for var, e in step.get("extract", {}).items()]
            for where, strings in texts:
                for text in strings:
                    for kind, value, var in PLACEHOLDER.findall(text):
                        if var:
                            uses.append((name, var, where))
                        elif kind not in KINDS:
                            errors.append(Problem(where, f"unknown placeholder {{{kind}:{value}}}; use test, pool, gen or env"))
                        elif kind == "test" and value not in rules:
                            errors.append(Problem(where, f"{{test:{value}}} names no test rule"))
                        elif kind == "gen" and value not in GENERATORS:
                            errors.append(Problem(where, f"{{gen:{value}}} is not a known generator"))
        if "skip" in node:
            skip = node["skip"]
            if not _request(skip["request"]).path.startswith(SKIP_PREFIX):
                errors.append(Problem(f"nodes.{name}.skip.request", f"a skip must call an {SKIP_PREFIX} endpoint"))
            headers = {k.lower(): v for k, v in skip["headers"].items()}
            if headers.get(KEY_HEADER.lower()) != KEY_VALUE:
                errors.append(Problem(f"nodes.{name}.skip.headers", f"a skip must send {KEY_HEADER}: {KEY_VALUE}"))

    start = data["start"]
    if start in nodes:
        reachable = _reachable(edges, start)
        for name in nodes:
            if name not in reachable:
                warnings.append(Problem(f"nodes.{name}", "not reachable from start"))

    for name, var, where in uses:
        sources = extractors.get(var)
        if not sources:
            errors.append(Problem(where, f"{{{var}}} is never extracted"))
        elif start in nodes and not nodes[name].get("requires"):
            # Without a requires flag to hold it back, the node can run on a path where
            # no earlier step has extracted the value yet.
            if name in _reachable(edges, start, stop=sources):
                warnings.append(Problem(where, f"{{{var}}} can be used before any step extracts it; add a requires flag"))

    return errors, warnings


def _request(text: str) -> Request:
    method, path = text.split(" ", 1)
    return Request(method, path)


def _extracts(raw: dict[str, Any]) -> dict[str, Extract]:
    return {name: Extract(e["path"], e["pick"], e["required"]) for name, e in raw.items()}


def _build(data: dict[str, Any], warnings: list[Problem]) -> LoadGraph:
    nodes = {}
    for name, raw in data["nodes"].items():
        skip = None
        if "skip" in raw:
            s = raw["skip"]
            skip = Skip(
                reason=s["reason"],
                request=_request(s["request"]),
                headers=dict(s["headers"]),
                body=s.get("body"),
                extract=_extracts(s.get("extract", {})),
                sets=tuple(s.get("sets", [])),
                clears=tuple(s.get("clears", [])),
            )
        nodes[name] = Node(
            name=name,
            request=_request(raw["request"]),
            body=raw.get("body"),
            extract=_extracts(raw.get("extract", {})),
            sets=tuple(raw.get("sets", [])),
            clears=tuple(raw.get("clears", [])),
            requires=tuple(raw.get("requires", [])),
            requires_not=tuple(raw.get("requires_not", [])),
            skip=skip,
        )
    return LoadGraph(
        murmur_version=data["murmur_version"],
        start=data["start"],
        session_flags=tuple(data["session_flags"]),
        test_rules={
            name: TestRule(name, r["description"], r.get("generate"), r.get("value"), r["source"], r["dev_only"])
            for name, r in data["test_rules"].items()
        },
        nodes=nodes,
        edges={name: tuple(Edge(e["to"], float(e["p"]), e["tag"]) for e in out) for name, out in data["edges"].items()},
        personas={
            name: Persona(name, float(p["share"]), {t: float(m) for t, m in p.get("multipliers", {}).items()})
            for name, p in data["personas"].items()
        },
        warnings=tuple(warnings),
    )
