"""Walk one simulated user through a load graph.

A Session holds one simulated user's state: its persona, the node it last ran, the
flags it has set and the values it has extracted. It never sends a request itself.
Each step goes:

    name = session.next()           # a node, or EXIT to end the session
    step = session.prepare(name)    # the request, with every placeholder filled in
    ...send step, or fake a response...
    session.complete(step, found)   # found: extract_values(step, body, rng), or None on failure

A session starts with its persona's flags, has the flag @user while it has an account,
leased or created, and @in:<group> while that account is in the pool group. Choosing the next node keeps only the edges whose target the
session can enter (its requires flags are all set, its requires_not flags all unset,
and every board it reads has a value), weights each by p times the
persona's multiplier for its tag, and draws from what is left. Edges to exit are always
kept.

A step fails when its request fails. When the request works, its extracts' unlocks and
locks always follow what the response showed. The step's own sets and clears, its new
account and its board posts only apply when every required extract found a value. Either
way the session moves to that node, like a user looking at the page, and chooses its
next step from there.
"""

from __future__ import annotations

import os
import random
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import jsonpath

from .board import Board
from .generators import generate
from .graph import EXIT, IN_GROUP, PLACEHOLDER, SESSION, USER, AccountSpec, Extract, LoadGraph, Persona, TestRule

RAND_LENGTH = 12


class PlaceholderError(Exception):
    """A placeholder in a step has no value, so the step cannot be sent."""


@dataclass(frozen=True)
class Step:
    """A request ready to send. When the node has a skip, the skip's request, extracts
    and flags replace the node's own, and via_skip is True; the account, posts and
    joins come from the skip when it has its own, and from the node otherwise. headers holds
    the graph's top-level headers whose values exist, then the skip's headers, which win
    on a clash."""

    node: str
    method: str
    path: str
    headers: dict[str, str]
    body: Any
    extracts: dict[str, Extract]
    sets: tuple[str, ...]
    clears: tuple[str, ...]
    via_skip: bool
    account: AccountSpec | None = None
    post: dict[str, str] | None = None
    joins: tuple[str, ...] = ()
    # The values this step's placeholders got, so its account and posts reuse them.
    values: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def pick_persona(graph: LoadGraph, rng: random.Random) -> Persona:
    """A persona with a share, drawn by share. Personas with a fixed count are run by
    murmur swarm on their own, or chosen by name."""
    personas = [p for p in graph.personas.values() if p.share is not None]
    return rng.choices(personas, weights=[p.share for p in personas])[0]


def extract_values(
    step: Step, body: Any, rng: random.Random, headers: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The values a response gives for the step's extracts, from its body by JSONPath or
    from its headers, matched in any case. A variable that matches nothing is left out."""
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    found = {}
    for var, extract in step.extracts.items():
        if extract.header is not None:
            if extract.header.lower() in lowered:
                found[var] = lowered[extract.header.lower()]
            continue
        matches = jsonpath.findall(extract.path, body) if body is not None else []
        if matches:
            found[var] = matches[0] if extract.pick == "first" else rng.choice(matches)
    return found


class Session:
    def __init__(
        self,
        graph: LoadGraph,
        persona: Persona,
        rng: random.Random,
        *,
        pool: dict[str, Mapping[str, Any]] | None = None,
        env: Mapping[str, str] | None = None,
        now: Callable[[], datetime] | None = None,
        board: Board | None = None,
        groups: set[str] | frozenset[str] = frozenset(),
    ):
        self.graph = graph
        self.persona = persona
        self.rng = rng
        self.pool = pool if pool is not None else {}
        self.env = env if env is not None else os.environ
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.board = board if board is not None else Board()
        self.flags: set[str] = set(persona.flags)
        self.values: dict[str, Any] = {}
        self.node: str | None = None
        self.problems: list[str] = []
        # Accounts this session created, which it uses before the ones it was given.
        self.own: dict[str, dict[str, Any]] = {}
        self._own_groups: set[str] = set()
        self._joined_all: list[str] = []  # every group the leased account joined
        # A created account waiting for its ready flag: its spec, fields and extra groups.
        self._pending: tuple[AccountSpec, dict[str, Any], list[str]] | None = None
        self._ready: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self._joined: list[str] = []
        self._leased_groups = set(groups)  # the pool groups of the account it was given

    def has_user(self) -> bool:
        return "user" in self.own or "user" in self.pool

    def groups(self) -> set[str]:
        """The pool groups the session's account is in, including groups it joined."""
        if "user" in self.own:
            if self._pending is not None:
                spec, _, joined = self._pending
                return {spec.group, *joined}
            return set(self._own_groups)
        if not self.has_user():
            return set()
        return self._leased_groups | set(self._joined_all)

    def can_enter(self, name: str) -> bool:
        if name == EXIT:
            return True
        node = self.graph.nodes[name]
        flags = set(self.flags)
        if self.has_user():
            flags.add(USER)
            flags |= {IN_GROUP + group for group in self.groups()}
        return (
            set(node.requires) <= flags
            and not (set(node.requires_not) & flags)
            and all(self.board.has(board) for board in node.boards)
        )

    def choices(self) -> dict[str, float]:
        """The probability of each possible next node, after dropping edges the session
        cannot take and applying the persona's multipliers. Empty when every weight is 0."""
        if self.node is None:
            return {self.graph.start: 1.0} if self.can_enter(self.graph.start) else {}
        weights: dict[str, float] = {}
        for edge in self.graph.edges[self.node]:
            if self.can_enter(edge.to):
                weight = edge.p * self.persona.multipliers.get(edge.tag, 1.0)
                weights[edge.to] = weights.get(edge.to, 0.0) + weight
        total = sum(weights.values())
        if total <= 0:
            return {}
        return {name: w / total for name, w in weights.items() if w > 0}

    def next(self) -> str:
        """The next node to run, or EXIT when the session ends."""
        options = self.choices()
        if not options:
            return EXIT
        names = list(options)
        return self.rng.choices(names, weights=[options[n] for n in names])[0]

    def prepare(self, name: str) -> Step:
        """The request for node name with every placeholder filled in. Raises
        PlaceholderError when one has no value. The same placeholder gets the same value
        everywhere in one step, and a fresh one in the next step. A value taken from the
        board goes back if the step cannot be prepared."""
        node = self.graph.nodes[name]
        source = node.skip or node
        method, path = source.request.method, source.request.path
        cache: dict[str, Any] = {}
        try:
            headers: dict[str, str] = {}
            for key, value in self.graph.headers.items():
                try:
                    headers[key] = _as_text(self._render(value, cache))
                except PlaceholderError:
                    pass  # sent once its value exists, such as a token before login
            for key, value in (node.skip.headers if node.skip else {}).items():
                headers = {k: v for k, v in headers.items() if k.lower() != key.lower()}
                headers[key] = _as_text(self._render(value, cache))
            return Step(
                node=name,
                method=method,
                path=self._render(path, cache),
                headers=headers,
                body=self._render(source.body, cache),
                extracts={
                    var: Extract(
                        self._render(e.path, cache) if e.path else None, e.pick, e.required, e.header,
                        e.unlocks, e.locks,
                    )
                    for var, e in source.extract.items()
                },
                sets=source.sets,
                clears=source.clears,
                via_skip=node.skip is not None,
                # The step's outcome, the same whether a skip performs it or not: taken
                # from the skip when it has its own, otherwise from the node.
                account=source.account or node.account,
                post=source.post or node.post,
                joins=source.joins or node.joins,
                values=cache,
            )
        except PlaceholderError:
            self._give_back(cache)
            raise

    def _give_back(self, values: dict[str, Any]) -> None:
        """Return the board values a step took, for a step that was never answered."""
        for token, value in values.items():
            if token.startswith("{board:"):
                self.board.give_back(token[len("{board:") : -1], value)

    def complete(self, step: Step, found: Mapping[str, Any] | None) -> bool:
        """Apply a step's result. found holds the extracted values, or is None when the
        request failed. Returns whether the step succeeded with every required value."""
        self.node = step.node
        if found is None:
            self._give_back(step.values)  # a failed request used nothing it took from the board
            return False
        for var in step.extracts:
            if var in found:
                self.values[var] = found[var]
            else:
                self.values.pop(var, None)  # an extract that found nothing this time
        ok = not any(e.required and var not in found for var, e in step.extracts.items())
        if ok:
            for flag in step.clears:
                if flag == SESSION:
                    self.flags -= set(self.graph.session_flags)
                else:
                    self.flags.discard(flag)
            self.flags |= set(step.sets)
        # Unlocks and locks follow what this response showed, whatever else happened.
        for var, extract in step.extracts.items():
            if var in found:
                self.flags |= set(extract.unlocks)
                self.flags -= set(extract.locks)
            else:
                self.flags -= set(extract.unlocks)
        if ok:
            self._created(step)
            self._join(step)
            self._post(step)
        self._check_ready()
        return ok

    def fail(self, name: str) -> None:
        """Record that node name could not be sent, for example because a placeholder
        had no value. The session moves to the node without changing any state."""
        self.node = name

    def take_ready_accounts(self) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
        """Accounts this session created that are now usable, as (groups, fields), each
        returned once."""
        ready, self._ready = self._ready, []
        return ready

    def take_joins(self) -> list[str]:
        """Groups the session's leased account joined since the last call."""
        joined, self._joined = self._joined, []
        return joined

    def _created(self, step: Step) -> None:
        """A step that creates an account makes it this session's user at once. It joins
        the pool once the session sets the account's ready flag."""
        if step.account is None:
            return
        try:
            cache = dict(step.values)
            fields = {k: _as_text(self._render(v, cache)) for k, v in step.account.fields.items()}
        except PlaceholderError as e:
            self.problems.append(f"{step.node}: account not created: {e}")
            return
        self.own["user"] = fields
        self._pending = (step.account, fields, [])

    def _join(self, step: Step) -> None:
        """A step that grants a role adds its groups to the account the session uses: a
        created account waiting to be ready, or the leased one."""
        if not step.joins:
            return
        if self._pending is not None:
            self._pending[2].extend(g for g in step.joins if g not in self._pending[2])
        elif "user" in self.own:
            self._own_groups |= set(step.joins)
            self._ready.append((tuple(self._own_groups), self.own["user"]))
        elif self.has_user():
            self._joined.extend(step.joins)
            self._joined_all.extend(step.joins)
        else:
            self.problems.append(f"{step.node}: no account to join {', '.join(step.joins)}")

    def _post(self, step: Step) -> None:
        for name, template in (step.post or {}).items():
            try:
                self.board.post(name, self._render(template, dict(step.values)))
            except PlaceholderError as e:
                self.problems.append(f"{step.node}: nothing posted to {name}: {e}")

    def _check_ready(self) -> None:
        if self._pending is None:
            return
        spec, fields, joined = self._pending
        if spec.ready is None or spec.ready in self.flags:
            groups = tuple(dict.fromkeys([spec.group, *joined]))
            self._ready.append((groups, fields))
            self._own_groups = set(groups)
            self._pending = None

    def _render(self, value: Any, cache: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return self._render_text(value, cache)
        if isinstance(value, list):
            return [self._render(item, cache) for item in value]
        if isinstance(value, dict):
            return {self._render_text(k, cache, as_text=True): self._render(v, cache) for k, v in value.items()}
        return value

    def _render_text(self, text: str, cache: dict[str, Any], as_text: bool = False) -> Any:
        whole = PLACEHOLDER.fullmatch(text)
        if whole and not as_text:
            # A string that is only a placeholder keeps the value's own JSON type, so a
            # numeric id stays a number in a request body.
            return self._value(whole, cache)
        return PLACEHOLDER.sub(lambda m: _as_text(self._value(m, cache)), text)

    def _value(self, match, cache: dict[str, Any]) -> Any:
        token = match.group(0)
        if token not in cache:
            kind, value, var = match.groups()
            cache[token] = self._resolve(kind, value, var)
        return cache[token]

    def _resolve(self, kind: str | None, value: str | None, var: str | None) -> Any:
        if var:
            if var not in self.values:
                raise PlaceholderError(f"{{{var}}} has not been extracted")
            return self.values[var]
        if kind == "test":
            return _test_value(self.graph.test_rules[value], self.rng)
        if kind == "gen":
            return generate(value, self.rng, self.now())
        if kind == "env":
            try:
                return self.env[value]
            except KeyError:
                raise PlaceholderError(f"{{env:{value}}}: {value} is not set") from None
        if kind == "board":
            taken = self.board.take(value)
            if taken is None:
                raise PlaceholderError(f"{{board:{value}}}: nothing on the board yet")
            return taken
        account, _, field_name = value.partition(".")
        try:
            fields = self.own[account] if account in self.own else self.pool[account]
        except KeyError:
            raise PlaceholderError(f"{{pool:{value}}}: this session has no {account} account") from None
        try:
            return fields[field_name]
        except KeyError:
            raise PlaceholderError(f"{{pool:{value}}}: the {account} account has no {field_name}") from None


def _test_value(rule: TestRule, rng: random.Random) -> str:
    """A value that triggers a test rule. Generated ones stand for new emails and phone
    numbers, which must be unique across runs and processes, so they come from real
    randomness rather than the seed."""
    if rule.value is not None:
        return rule.value
    rand = "".join(secrets.choice(string.ascii_lowercase) for _ in range(RAND_LENGTH))
    return "".join(secrets.choice(string.digits) if c == "#" else c for c in rule.generate.replace("{rand}", rand))


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
