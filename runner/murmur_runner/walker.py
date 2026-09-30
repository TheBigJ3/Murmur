"""Walk one simulated user through a load graph.

A Session holds one simulated user's state: its persona, the node it last ran, the
flags it has set and the values it has extracted. It never sends a request itself.
Each step goes:

    name = session.next()           # a node, or EXIT to end the session
    step = session.prepare(name)    # the request, with every placeholder filled in
    ...send step, or fake a response...
    session.complete(step, found)   # found: extract_values(step, body, rng), or None on failure

Choosing the next node keeps only the edges whose target the session can enter (its
requires flags are all set and its requires_not flags all unset), weights each by p
times the persona's multiplier for its tag, and draws from what is left. Edges to exit
are always kept.

A step fails when its request fails or a required extract finds nothing. A failed step
changes no flags and stores no values, but the session still moves to that node, like
a user looking at an error page, and chooses its next step from there.
"""

from __future__ import annotations

import os
import random
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import jsonpath

from .generators import generate
from .graph import EXIT, PLACEHOLDER, SESSION, Extract, LoadGraph, Persona, TestRule

RAND_LENGTH = 12


class PlaceholderError(Exception):
    """A placeholder in a step has no value, so the step cannot be sent."""


@dataclass(frozen=True)
class Step:
    """A request ready to send. When the node has a skip, the skip's request, headers,
    extracts and flags replace the node's own, and via_skip is True."""

    node: str
    method: str
    path: str
    headers: dict[str, str]
    body: Any
    extracts: dict[str, Extract]
    sets: tuple[str, ...]
    clears: tuple[str, ...]
    via_skip: bool


def pick_persona(graph: LoadGraph, rng: random.Random) -> Persona:
    personas = list(graph.personas.values())
    return rng.choices(personas, weights=[p.share for p in personas])[0]


def extract_values(step: Step, body: Any, rng: random.Random) -> dict[str, Any]:
    """The values a response body gives for the step's extracts. A variable whose
    JSONPath matches nothing is left out."""
    found = {}
    for var, extract in step.extracts.items():
        matches = jsonpath.findall(extract.path, body)
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
        pool: Mapping[str, Mapping[str, Any]] | None = None,
        env: Mapping[str, str] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.graph = graph
        self.persona = persona
        self.rng = rng
        self.pool = pool if pool is not None else {}
        self.env = env if env is not None else os.environ
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.flags: set[str] = set()
        self.values: dict[str, Any] = {}
        self.node: str | None = None

    def can_enter(self, name: str) -> bool:
        if name == EXIT:
            return True
        node = self.graph.nodes[name]
        return set(node.requires) <= self.flags and not (set(node.requires_not) & self.flags)

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
        everywhere in one step, and a fresh one in the next step."""
        node = self.graph.nodes[name]
        source = node.skip or node
        method, path = source.request.method, source.request.path
        cache: dict[str, Any] = {}
        headers = node.skip.headers if node.skip else {}
        return Step(
            node=name,
            method=method,
            path=self._render(path, cache),
            headers={self._render(k, cache): str(self._render(v, cache)) for k, v in headers.items()},
            body=self._render(source.body, cache),
            extracts={
                var: Extract(self._render(e.path, cache), e.pick, e.required) for var, e in source.extract.items()
            },
            sets=source.sets,
            clears=source.clears,
            via_skip=node.skip is not None,
        )

    def complete(self, step: Step, found: Mapping[str, Any] | None) -> bool:
        """Apply a step's result. found holds the extracted values, or is None when the
        request failed. Returns whether the step succeeded."""
        self.node = step.node
        if found is None:
            return False
        if any(e.required and var not in found for var, e in step.extracts.items()):
            return False
        for var in step.extracts:
            if var in found:
                self.values[var] = found[var]
            else:
                self.values.pop(var, None)  # an optional extract that found nothing
        for flag in step.clears:
            if flag == SESSION:
                self.flags -= set(self.graph.session_flags)
            else:
                self.flags.discard(flag)
        self.flags |= set(step.sets)
        return True

    def fail(self, name: str) -> None:
        """Record that node name could not be sent, for example because a placeholder
        had no value. The session moves to the node without changing any state."""
        self.node = name

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
        account, _, field = value.partition(".")
        try:
            return self.pool[account][field]
        except KeyError:
            raise PlaceholderError(f"{{pool:{value}}}: the leased accounts have no {value}") from None


def _test_value(rule: TestRule, rng: random.Random) -> str:
    if rule.value is not None:
        return rule.value
    rand = "".join(rng.choice(string.ascii_lowercase) for _ in range(RAND_LENGTH))
    return "".join(rng.choice(string.digits) if c == "#" else c for c in rule.generate.replace("{rand}", rand))


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
