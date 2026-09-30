"""The Locust side of murmur swarm.

murmur swarm runs Locust with this file and passes its settings in MURMUR_* environment
variables. Each Locust user is an Actor that walks sessions through the graph, one step
per task, and waits a think time between steps. A persona with a count gets a Locust
user class of its own with that fixed count; the other users draw personas by share.
Locust groups its statistics by node name. When the run stops, each process prints a
Murmur summary: sessions by persona, how they ended, sessions that found no free pool
account, accounts that joined the pool, steps that found nothing to extract, and the
reasons steps failed.
"""

from __future__ import annotations

import itertools
import os
import random
import types

from locust import HttpUser, between, events, task

from murmur_runner.actor import Actor
from murmur_runner.board import Board
from murmur_runner.graph import EXIT, load_graph
from murmur_runner.http import Judgement, judge
from murmur_runner.pool import Pool, drop_incomplete_grown, growable_groups
from murmur_runner.swarm import Tally, parse_shard, parse_think
from murmur_runner.walker import PlaceholderError


def _load_pool() -> Pool | None:
    path = os.environ.get("MURMUR_POOL") or None
    grown = os.environ.get("MURMUR_GROWN") or None
    others = [p for p in os.environ.get("MURMUR_GROWN_OTHERS", "").split(os.pathsep) if p]
    if path is None and grown is None:
        return None
    pool = Pool.load(path, grown, others)
    shard = os.environ.get("MURMUR_POOL_SHARD")
    if shard:
        pool = pool.shard(*parse_shard(shard))
    drop_incomplete_grown(GRAPH, pool)  # murmur swarm already printed a note about them
    return pool


GRAPH = load_graph(os.environ["MURMUR_GRAPH"])
GROW = os.environ.get("MURMUR_GROW", "1") == "1"
POOL = _load_pool()
if POOL is not None and not GROW:
    POOL.grown_path = None  # read the saved accounts, but save no new ones
BOARD = Board()
THINK = parse_think(os.environ.get("MURMUR_THINK", "1-5"))
SEED = int(os.environ.get("MURMUR_SEED", "0"))
MAX_STEPS = int(os.environ.get("MURMUR_MAX_STEPS", "200"))
TALLY = Tally()
_numbers = itertools.count(1)


@events.quitting.add_listener
def _print_summary(environment, **kwargs):
    for line in TALLY.lines(growable=set(growable_groups(GRAPH))):
        print(line)


class MurmurUser(HttpUser):
    abstract = True
    wait_time = between(*THINK)
    persona_name: str | None = None  # a fixed persona, or None to draw one by share

    def on_start(self):
        rng = random.Random(f"{SEED}:{os.getpid()}:{next(_numbers)}")
        self.actor = Actor(GRAPH, rng, pool=POOL, board=BOARD, env=os.environ, persona=self.persona_name, grow=GROW)
        self.steps = 0

    def on_stop(self):
        self._end(None)

    @task
    def step(self):
        actor = self.actor
        if actor.session is None:
            self._begin()
        session = actor.session
        name = session.next()
        if name == EXIT or self.steps >= MAX_STEPS:
            self._end("exit" if name == EXIT else "step limit")
            return
        self.steps += 1
        try:
            step = session.prepare(name)
        except PlaceholderError as e:
            session.fail(name)
            TALLY.failed(name, str(e))
            self.environment.events.request.fire(
                request_type="STEP", name=name, response_time=0, response_length=0,
                exception=e, context={}, response=None,
            )
            return
        with self.client.request(
            step.method, step.path, name=name, headers=step.headers,
            json=step.body, catch_response=True,
        ) as response:
            if response.status_code == 0:
                verdict = Judgement(None, f"request failed: {response.error}")
            else:
                verdict = judge(step, response.status_code, response.headers, response.text or "", actor.rng)
            if verdict.error:
                response.failure(verdict.error)
                TALLY.failed(name, verdict.error)
            else:
                response.success()
                if verdict.empty:
                    TALLY.found_nothing(name, verdict.empty)
        session.complete(step, verdict.found)
        settled = actor.settle()
        TALLY.grown += len(settled.new)
        TALLY.joined += len(settled.joined)
        for problem in session.problems:
            TALLY.note(problem)
        session.problems.clear()

    def _begin(self):
        self.client.cookies.clear()  # every session starts logged out, like a new visitor
        session = self.actor.begin()
        if POOL is not None and self.actor.account is None:
            TALLY.no_account[session.persona.pool] += 1
        self.steps = 0
        TALLY.personas[session.persona.name] += 1

    def _end(self, reason: str | None):
        if reason:
            TALLY.ended[reason] += 1
        self.actor.end()


def _user_class(name: str, attributes: dict) -> type:
    def body(namespace):
        namespace.update(attributes, __module__=__name__)

    return types.new_class(name, (MurmurUser,), exec_body=body)


# One user class per persona with a fixed count, and one for the personas with a share.
# Locust finds them among this module's globals.
for _persona in GRAPH.personas.values():
    if _persona.count is not None:
        globals()[f"Murmur_{_persona.name}"] = _user_class(
            f"Murmur_{_persona.name}", {"fixed_count": _persona.count, "persona_name": _persona.name}
        )
if any(p.share is not None for p in GRAPH.personas.values()):
    Murmur_shared = _user_class("Murmur_shared", {"weight": 1, "persona_name": None})
