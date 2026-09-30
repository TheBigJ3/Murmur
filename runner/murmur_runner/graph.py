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
USER = "@user"  # set while a session has an account, leased or created
IN_GROUP = "@in:"  # "@in:<group>" is set while the session's account is in that pool group
# Probabilities and persona shares must sum to 1 within this, which absorbs float
# rounding (0.1 + 0.2 + 0.7) but not a mistake (0.33 + 0.33 + 0.33).
TOLERANCE = 1e-6
SKIP_PREFIX = "/internal/murmur/"
KEY_HEADER = "X-Murmur-Key"
KEY_VALUE = "{env:MURMUR_KEY}"

# {kind:value} for test rules, the account pool, generated inputs, environment
# variables and the shared board, or {name} for a value extracted by an earlier step.
PLACEHOLDER = re.compile(r"\{([a-z]+):([^{}\s]+)\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
KINDS = ("test", "pool", "gen", "env", "board")
DEFAULT_GROUP = "default"
POOL_ROLES = ("user", "other_user")
# The parts of a node that its skip replaces: with a skip, the node's own are never used.
REPLACED_BY_SKIP = ("sets", "clears", "extract", "body")

_PATTERN_MESSAGES = {
    "^[0-9]+\\.[0-9]+\\.[0-9]+$": "is not an X.Y.Z version",
    "^[A-Za-z0-9_.-]+$": "may only contain letters, digits, _, . and -",
    "^([A-Za-z0-9_.-]+|@session)$": "may only contain letters, digits, _, . and -, or be @session",
    "^([A-Za-z0-9_.-]+|@user|@in:[A-Za-z0-9_.-]+)$": "may only contain letters, digits, _, . and -, or be @user or @in:<group>",
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
    """A value to keep from a response: from the body by JSONPath (path and pick), or
    from a response header (header). unlocks are flags that follow whether it found a
    value, set when it did and cleared when it did not; locks are flags cleared when it
    found one."""

    path: str | None
    pick: Literal["random", "first"]
    required: bool
    header: str | None = None
    unlocks: tuple[str, ...] = ()
    locks: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountSpec:
    """An account a step creates, joining pool group group once the session sets ready."""

    group: str
    fields: dict[str, str]
    ready: str | None


@dataclass(frozen=True)
class Skip:
    reason: str
    request: Request
    headers: dict[str, str]
    body: Any
    extract: dict[str, Extract]
    sets: tuple[str, ...]
    clears: tuple[str, ...]
    account: AccountSpec | None = None
    post: dict[str, str] | None = None
    joins: tuple[str, ...] = ()


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
    account: AccountSpec | None = None
    post: dict[str, str] | None = None
    joins: tuple[str, ...] = ()
    boards: tuple[str, ...] = ()  # board names this node takes a value from


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
    """share splits the users left after fixed counts; count is a fixed number of users."""

    name: str
    share: float | None
    multipliers: dict[str, float]
    count: int | None = None
    pool: str = DEFAULT_GROUP
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadGraph:
    murmur_version: str
    start: str
    session_flags: tuple[str, ...]
    test_rules: dict[str, TestRule]
    headers: dict[str, str]
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
        problems.extend(_schema_problems(error))
    return sorted(problems, key=lambda p: (p.location, p.message))


def _schema_problems(error: Any) -> list[Problem]:
    location = _location(error.absolute_path)
    if error.validator == "pattern":
        return [Problem(location, f"{error.instance!r} {_PATTERN_MESSAGES.get(error.validator_value, error.message)}")]
    if error.validator == "not":
        return [Problem(location, f"{error.instance!r} is reserved for leaving the graph and cannot be a node name")]
    if error.validator == "oneOf":
        if error.absolute_path and error.absolute_path[0] == "test_rules":
            return [Problem(location, "a test rule needs exactly one of generate or value")]
        if error.absolute_path and error.absolute_path[0] == "personas":
            return [Problem(location, "a persona needs exactly one of share or count")]
        # An extract: report what is wrong with the form it was meant to be, a header
        # extract when it names a header, and a body extract otherwise.
        instance = error.instance if isinstance(error.instance, dict) else {}
        if "header" in instance and "path" in instance:
            return [Problem(location, "an extract reads either a path or a header, not both")]
        branch = 1 if "header" in instance else 0 if "path" in instance else None
        if branch is None:
            return [Problem(location, "an extract needs either path and pick, or header")]
        problems = []
        for sub in error.context:
            if sub.relative_schema_path[0] == branch:
                problems.extend(_schema_problems(sub))
        return problems
    return [Problem(location, error.message)]


def _format_number(value: float) -> str:
    return f"{value:.10g}"


def _sums_to_one(values: list[float]) -> bool:
    return math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=TOLERANCE)


def _live(name: str, node: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Like _steps, but a node with a skip only contributes what the skip does not replace:
    its account, joins and post."""
    if "skip" in node:
        yield f"nodes.{name}", {k: v for k, v in node.items() if k in ("account", "joins", "post")}
        yield f"nodes.{name}.skip", node["skip"]
    else:
        yield f"nodes.{name}", node


def _steps(name: str, node: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """The node itself and its skip, if any, each with its location."""
    yield f"nodes.{name}", node
    if "skip" in node:
        yield f"nodes.{name}.skip", node["skip"]


def json_strings(value: Any) -> Iterator[str]:
    """Every string in a JSON value, including object keys."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from json_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from json_strings(item)


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

    shares = [p["share"] for p in personas.values() if "share" in p]
    if not shares:
        errors.append(Problem("personas", "at least one persona needs a share, for the users beyond fixed counts"))
    elif not _sums_to_one(shares):
        errors.append(Problem("personas", f"shares sum to {_format_number(math.fsum(shares))}, not 1"))
    tags = {e["tag"] for out in edges.values() for e in out}
    for pname, persona in personas.items():
        for tag in persona.get("multipliers", {}):
            if tag not in tags:
                errors.append(Problem(f"personas.{pname}.multipliers.{tag}", f"no edge has the tag '{tag}'"))

    # A flag can be set by a step, by an extract that unlocks it, or by a persona.
    flags_set = {f for p in personas.values() for f in p.get("flags", [])}
    for name, node in nodes.items():
        for _, step in _live(name, node):
            flags_set.update(step.get("sets", []))
            for e in step.get("extract", {}).values():
                flags_set.update(e.get("unlocks", []))
    posted = {board for name, node in nodes.items() for _, step in _live(name, node) for board in step.get("post", {})}
    read: set[str] = set()
    groups_known = {p.get("pool", DEFAULT_GROUP) for p in personas.values()}
    for name, node in nodes.items():
        for _, step in _steps(name, node):
            if "account" in step:
                groups_known.add(step["account"].get("group", DEFAULT_GROUP))
            groups_known.update(step.get("joins", []))

    def check_flags(loc: str, flags: list[str]) -> None:
        for flag in flags:
            if flag.startswith(IN_GROUP):
                if flag[len(IN_GROUP):] not in groups_known:
                    errors.append(Problem(loc, f"{flag}: no persona, account or joins uses the pool group {flag[len(IN_GROUP):]}"))
            elif flag not in (SESSION, USER) and flag not in flags_set:
                errors.append(Problem(loc, f"flag '{flag}' is never set"))

    check_flags("session_flags", data["session_flags"])
    uses: list[tuple[str, str, str]] = []  # (node, variable, location)
    extractors: dict[str, set[str]] = {}

    def scan(where: str, strings: Iterator[str] | list[str], node: str | None, board: bool = True) -> None:
        """Check every placeholder in strings. node is None where a value is only used
        once it exists, so no 'before it is extracted' warning applies. board is False
        where the shared board cannot be read."""
        for text in strings:
            for kind, value, var in PLACEHOLDER.findall(text):
                if var:
                    uses.append((node, var, where))
                elif kind not in KINDS:
                    errors.append(Problem(where, f"unknown placeholder {{{kind}:{value}}}; use test, pool, gen, env or board"))
                elif kind == "test" and value not in rules:
                    errors.append(Problem(where, f"{{test:{value}}} names no test rule"))
                elif kind == "gen" and value not in GENERATORS:
                    errors.append(Problem(where, f"{{gen:{value}}} is not a known generator"))
                elif kind == "pool" and not value.partition(".")[2]:
                    errors.append(Problem(where, f"{{pool:{value}}} needs a field, such as {{pool:user.email}}"))
                elif kind == "pool" and value.partition(".")[0] not in POOL_ROLES:
                    errors.append(Problem(where, f"{{pool:{value}}}: the pool provides only user and other_user"))
                elif kind == "board" and not board:
                    errors.append(Problem(where, f"{{board:{value}}} can only be used in a step's request, headers or body"))
                elif kind == "board" and value not in posted:
                    errors.append(Problem(where, f"{{board:{value}}}: no step posts to the board {value}"))
                elif kind == "board":
                    read.add(value)

    for header, value in data.get("headers", {}).items():
        scan(f"headers.{header}", [header, value], None, board=False)
    for name, node in nodes.items():
        check_flags(f"nodes.{name}.requires", node.get("requires", []))
        check_flags(f"nodes.{name}.requires_not", node.get("requires_not", []))
        for both in sorted(set(node.get("requires", [])) & set(node.get("requires_not", []))):
            errors.append(Problem(f"nodes.{name}", f"can never be entered: {both} is both required and forbidden"))
        if "skip" in node:
            for part in REPLACED_BY_SKIP:
                if part in node:
                    warnings.append(Problem(
                        f"nodes.{name}.{part}",
                        "never used: the node's skip replaces its request and results; put it in the skip",
                    ))
        for loc, step in _live(name, node):
            check_flags(f"{loc}.clears", step.get("clears", []))
            for var in step.get("extract", {}):
                extractors.setdefault(var, set()).add(name)
            # Placeholders can sit in the request, headers and body, and inside an
            # extract's JSONPath, such as a filter on a value extracted earlier.
            for field in ("request", "headers", "body"):
                if field in step:
                    scan(f"{loc}.{field}", json_strings(step.get(field)), name)
            for var, e in step.get("extract", {}).items():
                if "path" in e:
                    scan(f"{loc}.extract.{var}.path", [e["path"]], name, board=False)
                check_flags(f"{loc}.extract.{var}.locks", e.get("locks", []))
            # An account and board posts are filled in after the step's own extracts.
            if "account" in step:
                scan(f"{loc}.account.fields", json_strings(step["account"]["fields"]), None, board=False)
                if "ready" in step["account"]:
                    check_flags(f"{loc}.account.ready", [step["account"]["ready"]])
            if "post" in step:
                scan(f"{loc}.post", json_strings(step["post"]), None, board=False)
        if "skip" in node:
            skip = node["skip"]
            if not _request(skip["request"]).path.startswith(SKIP_PREFIX):
                errors.append(Problem(f"nodes.{name}.skip.request", f"a skip must call an {SKIP_PREFIX} endpoint"))
            headers = {k.lower(): v for k, v in skip["headers"].items()}
            if headers.get(KEY_HEADER.lower()) != KEY_VALUE:
                errors.append(Problem(f"nodes.{name}.skip.headers", f"a skip must send {KEY_HEADER}: {KEY_VALUE}"))

    for name, node in nodes.items():
        for loc, step in _steps(name, node):
            for board in step.get("post", {}):
                if board not in read:
                    warnings.append(Problem(f"{loc}.post.{board}", f"no step reads the board {board}"))
    persona_groups = {p.get("pool", DEFAULT_GROUP) for p in personas.values()}
    for name, node in nodes.items():
        for loc, step in _steps(name, node):
            for group in step.get("joins", []):
                if group not in persona_groups:
                    warnings.append(Problem(f"{loc}.joins", f"no persona leases from the pool group {group}"))

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
        elif name is None:
            continue  # a top-level header is left out until its value exists
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
    return {
        name: Extract(
            e.get("path"), e.get("pick", "first"), e.get("required", False), e.get("header"),
            tuple(e.get("unlocks", [])), tuple(e.get("locks", [])),
        )
        for name, e in raw.items()
    }


def _account(raw: dict[str, Any] | None) -> AccountSpec | None:
    if raw is None:
        return None
    return AccountSpec(raw.get("group", DEFAULT_GROUP), dict(raw["fields"]), raw.get("ready"))


def _boards(sent: dict[str, Any]) -> tuple[str, ...]:
    """The board names a step takes values from, in the parts that are sent."""
    names = []
    for field in ("request", "headers", "body"):
        for text in json_strings(sent.get(field)):
            for kind, value, _ in PLACEHOLDER.findall(text):
                if kind == "board" and value not in names:
                    names.append(value)
    return tuple(names)


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
                account=_account(s.get("account")),
                post=dict(s["post"]) if "post" in s else None,
                joins=tuple(s.get("joins", [])),
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
            account=_account(raw.get("account")),
            post=dict(raw["post"]) if "post" in raw else None,
            joins=tuple(raw.get("joins", [])),
            boards=_boards(raw.get("skip", raw)),
        )
    return LoadGraph(
        murmur_version=data["murmur_version"],
        start=data["start"],
        session_flags=tuple(data["session_flags"]),
        headers=dict(data.get("headers", {})),
        test_rules={
            name: TestRule(name, r["description"], r.get("generate"), r.get("value"), r["source"], r["dev_only"])
            for name, r in data["test_rules"].items()
        },
        nodes=nodes,
        edges={name: tuple(Edge(e["to"], float(e["p"]), e["tag"]) for e in out) for name, out in data["edges"].items()},
        personas={
            name: Persona(
                name,
                float(p["share"]) if "share" in p else None,
                {t: float(m) for t, m in p.get("multipliers", {}).items()},
                int(p["count"]) if "count" in p else None,
                p.get("pool", DEFAULT_GROUP),
                tuple(p.get("flags", [])),
            )
            for name, p in data["personas"].items()
        },
        warnings=tuple(warnings),
    )
