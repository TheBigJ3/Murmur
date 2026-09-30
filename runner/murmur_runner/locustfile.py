"""The Locust side of murmur swarm.

murmur swarm runs Locust with this file and passes its settings in MURMUR_* environment
variables. Each Locust user walks sessions through the graph, one step per task, and
waits a think time between steps. Locust groups its statistics by node name. When the
run stops, each process prints a Murmur summary: sessions by persona, how they ended,
sessions that found no free pool account, steps that found nothing to extract, and the
reasons steps failed.
"""

from __future__ import annotations

import itertools
import os
import random

from locust import HttpUser, between, events, task

from murmur_runner.graph import EXIT, load_graph
from murmur_runner.http import Judgement, judge
from murmur_runner.pool import Pool
from murmur_runner.swarm import Tally, parse_shard, parse_think
from murmur_runner.walker import PlaceholderError, Session, pick_persona

def _load_pool() -> Pool | None:
    path = os.environ.get("MURMUR_POOL")
    if not path:
        return None
    pool = Pool.load(path)
    shard = os.environ.get("MURMUR_POOL_SHARD")
    if shard:
        pool = pool.shard(*parse_shard(shard))
    return pool


GRAPH = load_graph(os.environ["MURMUR_GRAPH"])
POOL = _load_pool()
THINK = parse_think(os.environ.get("MURMUR_THINK", "1-5"))
SEED = int(os.environ.get("MURMUR_SEED", "0"))
MAX_STEPS = int(os.environ.get("MURMUR_MAX_STEPS", "200"))
TALLY = Tally()
_numbers = itertools.count(1)


@events.quitting.add_listener
def _print_summary(environment, **kwargs):
    for line in TALLY.lines():
        print(line)


class MurmurUser(HttpUser):
    wait_time = between(*THINK)

    def on_start(self):
        self.rng = random.Random(SEED * 1_000_003 + next(_numbers))
        self.session: Session | None = None
        self.account = None
        self.steps = 0

    def on_stop(self):
        self._end(None)

    @task
    def step(self):
        if self.session is None:
            self._begin()
        name = self.session.next()
        if name == EXIT or self.steps >= MAX_STEPS:
            self._end("exit" if name == EXIT else "step limit")
            return
        self.steps += 1
        try:
            step = self.session.prepare(name)
        except PlaceholderError as e:
            self.session.fail(name)
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
                verdict = judge(step, response.status_code, response.headers, response.text or "", self.rng)
            if verdict.error:
                response.failure(verdict.error)
                TALLY.failed(name, verdict.error)
            else:
                response.success()
                if verdict.empty:
                    TALLY.found_nothing(name, verdict.empty)
        self.session.complete(step, verdict.found)

    def _begin(self):
        persona = pick_persona(GRAPH, self.rng)
        accounts = {}
        if POOL is not None:
            self.account = POOL.lease(self.rng)
            if self.account is None:
                TALLY.no_account += 1
            else:
                accounts["user"] = self.account.fields
            other = POOL.other(self.rng, self.account)
            if other is not None:
                accounts["other_user"] = other.fields
        self.client.cookies.clear()  # every session starts logged out, like a new visitor
        self.session = Session(GRAPH, persona, self.rng, pool=accounts, env=os.environ)
        self.steps = 0
        TALLY.personas[persona.name] += 1

    def _end(self, reason: str | None):
        if reason:
            TALLY.ended[reason] += 1
        if self.account is not None:
            POOL.release(self.account)
            self.account = None
        self.session = None
