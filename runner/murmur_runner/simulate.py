"""Dry runs: walk many sessions through a load graph without sending a request.

Every extract finds a stand-in value such as <productId>, and pool accounts and
environment variables get stand-ins too, so the walk follows the graph's
probabilities, flags and personas alone. A step still fails when it uses a value no
earlier step in that session extracted. The report shows the traffic mix a swarm
would produce, before any traffic is sent.
"""

from __future__ import annotations

import random
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .graph import EXIT, LoadGraph, Persona
from .walker import PlaceholderError, Session, pick_persona

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
    def __missing__(self, account: str):
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


def simulate_session(graph: LoadGraph, persona: Persona, rng: random.Random, max_steps: int) -> SessionTrace:
    session = Session(graph, persona, rng, pool=_StandInPool(), env=_StandIns("env:"))
    steps: list[StepResult] = []
    while len(steps) < max_steps:
        name = session.next()
        if name == EXIT:
            return SessionTrace(persona.name, tuple(steps), ENDED_EXIT)
        try:
            step = session.prepare(name)
        except PlaceholderError as e:
            session.fail(name)
            steps.append(StepResult(name, False, str(e)))
            continue
        session.complete(step, {var: f"<{var}>" for var in step.extracts})
        steps.append(StepResult(name, True))
    return SessionTrace(persona.name, tuple(steps), ENDED_LIMIT)


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


def simulate(
    graph: LoadGraph,
    sessions: int,
    seed: int,
    *,
    persona: str | None = None,
    max_steps: int = 500,
) -> Report:
    """Walk sessions with a fixed seed, so the same arguments give the same report.
    persona, when given, runs every session as that persona."""
    rng = random.Random(seed)
    traces = []
    for _ in range(sessions):
        chosen = graph.personas[persona] if persona else pick_persona(graph, rng)
        traces.append(simulate_session(graph, chosen, rng, max_steps))
    return Report(sessions, seed, traces)


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
