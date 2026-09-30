import json
import random

from murmur_runner.actor import Actor
from murmur_runner.board import Board
from murmur_runner.graph import parse_graph
from murmur_runner.pool import Pool

from test_walker import ENV, with_account


def actor_for(graph, pool, **options):
    loaded = parse_graph(json.dumps(graph))
    return Actor(loaded, random.Random(1), pool=pool, board=Board(), env=ENV, **options)


class TestActor:
    def test_leases_from_the_persona_group(self, graph):
        graph["personas"]["staff"] = {"count": 1, "pool": "staff"}
        pool = Pool([{"email": "c", "password": "x"}], groups={"staff": [{"email": "s", "password": "y"}]})
        actor = actor_for(graph, pool, persona="staff")

        session = actor.begin()

        assert actor.account.fields["email"] == "s"
        assert session.pool["user"]["email"] == "s"

    def test_adds_a_ready_account_and_leases_it_to_the_session(self, graph):
        with_account(graph)
        pool = Pool([{"email": "c", "password": "x"}])
        actor = actor_for(graph, pool)
        session = actor.begin()
        old = actor.account

        session.complete(session.prepare("signup"), {})
        added = actor.settle().new

        assert [(a.groups, a.grown) for a in added] == [({"customer"}, True)]
        assert actor.account == added[0]
        assert pool.free("default") == 1 and old.index not in {a.index for a in added}
        assert pool.free("customer") == 0

    def test_adds_nothing_when_growing_is_off(self, graph):
        with_account(graph)
        pool = Pool([{"email": "c", "password": "x"}])
        actor = actor_for(graph, pool, grow=False)
        session = actor.begin()

        session.complete(session.prepare("signup"), {})

        assert not actor.settle()
        assert len(pool.accounts) == 1
        assert session.own["user"]["email"].endswith("@test.com")  # the session still uses its new account

    def test_releases_its_account_at_the_end(self, graph):
        pool = Pool([{"email": "c", "password": "x"}])
        actor = actor_for(graph, pool)
        actor.begin()

        actor.end()

        assert pool.free() == 1 and actor.session is None

    def test_joins_its_leased_account_to_a_group(self, graph, tmp_path):
        graph["nodes"]["search"]["joins"] = ["owner"]
        pool = Pool([{"email": "c", "password": "x"}], grown_path=tmp_path / "pool.grown.json")
        actor = actor_for(graph, pool)
        session = actor.begin()

        session.complete(session.prepare("search"), {"productId": "p1"})

        settled = actor.settle()

        assert (settled.new, settled.joined) == ([], [(actor.account, ["owner"])])
        assert pool.group("owner") == [actor.account]

    def test_a_created_account_that_joins_later_counts_as_a_join(self, graph):
        with_account(graph, ready=None)
        graph["nodes"]["search"]["joins"] = ["owner"]
        pool = Pool()
        actor = actor_for(graph, pool)
        session = actor.begin()
        session.complete(session.prepare("signup"), {})
        assert len(actor.settle().new) == 1

        session.complete(session.prepare("search"), {"productId": "p1"})
        settled = actor.settle()

        assert (settled.new, [groups for _, groups in settled.joined]) == ([], [["owner"]])
        assert len(pool.accounts) == 1
