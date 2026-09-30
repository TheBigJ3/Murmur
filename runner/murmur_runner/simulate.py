"""Dry runs: walk many sessions through a load graph without sending a request.

Every extract finds a stand-in value such as <productId>, and pool accounts and
environment variables get stand-ins too, so the walk follows the graph's
probabilities, flags and personas alone. An extract that only unlocks or locks flags
finds a value half the time, so both the locked and the unlocked paths get walked.
Sessions share one board, as they do in a swarm. When a step can create an account, half the
sessions start without one, so the paths that sign up get walked as well as the ones
that log in. A persona with a fixed count runs count out of --users sessions. A step still fails when it uses a
value no earlier step in that session extracted. The report shows the traffic mix a
swarm would produce, before any traffic is sent.
"""

from __future__ import annotations

import random
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .board import Board
from .graph import EXIT, LoadGraph, Persona
from .walker import PlaceholderError, Session, Step, pick_persona

ENDED_EXIT = "exit"
ENDED_LIMIT = "step limit"


class _StandIns(dict):
    """Returns <prefix><key> for any key, in place of real accounts and variables."""

    def __init__(self, prefix: str):
        super().__init__()
        self.prefix = prefix

    def __missing__(self, key: str):
        return f"<{self.prefix}{key}>"


class _StandInPool(dict):
    """Stand-in accounts for every role except user, which a session only has when it
    was given one, as in a real run."""

    def __missing__(self, account: str):
        if account == "user":
            raise KeyError(account)
        return _StandIns(f"pool:{account}.")


@dataclass(frozen=True)
class StepResult:
    node: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class SessionTrace:
    persona: str
    steps: tuple[StepResult, ...]
    ended: str
    notes: tuple[str, ...] = ()


def _stand_in_values(step: Step, rng: random.Random) -> dict[str, str]:
    found = {}
    for var, extract in step.extracts.items():
        is_check = (extract.unlocks or extract.locks) and not extract.required
        if not is_check or rng.random() < 0.5:
            found[var] = f"<{var}>"
    return found


def simulate_session(
    graph: LoadGraph,
    persona: Persona,
    rng: random.Random,
    max_steps: int,
    board: Board | None = None,
    has_account: bool = True,
) -> SessionTrace:
    pool = _StandInPool()
    if has_account:
        pool["user"] = _StandIns("pool:user.")
    groups = {persona.pool} if has_account else set()
    session = Session(graph, persona, rng, pool=pool, env=_StandIns("env:"), board=board, groups=groups)
    steps: list[StepResult] = []
    while len(steps) < max_steps:
        name = session.next()
        if name == EXIT:
            return SessionTrace(persona.name, tuple(steps), ENDED_EXIT, tuple(session.problems))
        try:
            step = session.prepare(name)
        except PlaceholderError as e:
            session.fail(name)
            steps.append(StepResult(name, False, str(e)))
            continue
        session.complete(step, _stand_in_values(step, rng))
        steps.append(StepResult(name, True))
    return SessionTrace(persona.name, tuple(steps), ENDED_LIMIT, tuple(session.problems))


@dataclass
class Report:
    sessions: int
    seed: int
    traces: list[SessionTrace] = field(repr=False)

    def lengths(self) -> list[int]:
        return [len(t.steps) for t in self.traces]

    def visits(self) -> Counter[str]:
        return Counter(s.node for t in self.traces for s in t.steps)

    def failures(self) -> Counter[tuple[str, str]]:
        return Counter((s.node, s.error) for t in self.traces for s in t.steps if not s.ok)

    def ended(self) -> Counter[str]:
        return Counter(t.ended for t in self.traces)

    def notes(self) -> Counter[str]:
        return Counter(note for t in self.traces for note in t.notes)


def simulate(
    graph: LoadGraph,
    sessions: int,
    seed: int,
    *,
    persona: str | None = None,
    max_steps: int = 500,
    users: int = 100,
) -> Report:
    """Walk sessions with a fixed seed, so the same arguments give the same report.
    persona, when given, runs every session as that persona. Otherwise a persona with a
    fixed count runs count out of users sessions, as it would in a swarm of that size,
    and the rest are drawn by share."""
    rng = random.Random(seed)
    board = Board()
    creates = any(s.account for n in graph.nodes.values() for s in (n, n.skip) if s is not None)
    traces = []
    for _ in range(sessions):
        chosen = graph.personas[persona] if persona else _pick(graph, rng, users)
        has_account = rng.random() < 0.5 if creates else True
        traces.append(simulate_session(graph, chosen, rng, max_steps, board, has_account))
    return Report(sessions, seed, traces)


def _pick(graph: LoadGraph, rng: random.Random, users: int) -> Persona:
    fixed = [p for p in graph.personas.values() if p.count]
    draw = rng.random() * max(users, sum(p.count for p in fixed))
    for persona in fixed:
        if draw < persona.count:
            return persona
        draw -= persona.count
    return pick_persona(graph, rng)


def format_report(graph: LoadGraph, report: Report, source: str, show: int = 0) -> str:
    lines = [f"{source}: {report.sessions} simulated sessions, seed {report.seed}", ""]
    lengths = report.lengths()
    total = sum(lengths)

    lines.append("Personas")
    by_persona: dict[str, list[int]] = {}
    for t in report.traces:
        by_persona.setdefault(t.persona, []).append(len(t.steps))
    width = max(len(name) for name in graph.personas)
    for name in graph.personas:
        runs = by_persona.get(name, [])
        mean = f"{statistics.fmean(runs):.1f}" if runs else "-"
        lines.append(f"  {name:<{width}}  {len(runs):>6} sessions  {mean:>6} steps on average")
    lines.append("")

    ended = report.ended()
    lines.append("Session length (steps)")
    lines.append(
        f"  mean {statistics.fmean(lengths):.1f}, median {statistics.median(lengths):g}, "
        f"95th percentile {_percentile(lengths, 95)}, longest {max(lengths)}"
    )
    lines.append(f"  ended by exit: {ended[ENDED_EXIT]}, hit the step limit: {ended[ENDED_LIMIT]}")
    lines.append("")

    visits = report.visits()
    width = max(len(name) for name in graph.nodes)
    lines.append("Requests")
    lines.append(f"  {'node':<{width}}  {'total':>7}  {'per session':>11}  {'share':>6}")
    for name, count in sorted(visits.items(), key=lambda kv: (-kv[1], kv[0])):
        share = 100 * count / total if total else 0
        lines.append(f"  {name:<{width}}  {count:>7}  {count / report.sessions:>11.2f}  {share:>5.1f}%")
    never = [name for name in graph.nodes if name not in visits]
    if never:
        lines.append(f"  never requested: {', '.join(never)}")

    notes = report.notes()
    if notes:
        lines.append("")
        lines.append("Notes")
        for note, count in sorted(notes.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {note} ({count}x)")

    failures = report.failures()
    if failures:
        lines.append("")
        lines.append("Failed steps")
        for (node, error), count in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {node}: {error} ({count}x)")

    if show:
        lines.append("")
        lines.append("Example sessions")
        for t in report.traces[:show]:
            path = " > ".join(s.node if s.ok else f"{s.node} (failed)" for s in t.steps)
            lines.append(f"  {t.persona}: {path} > {t.ended}")
    return "\n".join(lines)


def _percentile(values: list[int], pct: int) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, (len(ordered) * pct) // 100)]
