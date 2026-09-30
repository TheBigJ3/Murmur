import json
import random

import pytest

from murmur_runner.graph import parse_graph
from murmur_runner.pool import Pool, PoolError, check_pool, pool_fields


def pool_of(*emails):
    return Pool([{"email": e, "password": "pw"} for e in emails])


class TestLoad:
    def test_loads_accounts(self, pool_file):
        pool = Pool.load(pool_file)

        assert [a.fields["email"] for a in pool.accounts] == ["pool1@test.com", "pool2@test.com"]
        assert [a.index for a in pool.accounts] == [1, 2]

    def test_reports_a_missing_file(self, tmp_path):
        with pytest.raises(PoolError, match="cannot read the file: No such file or directory$"):
            Pool.load(tmp_path / "pool.json")

    def test_reports_invalid_json(self, tmp_path):
        (tmp_path / "pool.json").write_text("{accounts: []}")

        with pytest.raises(PoolError, match="not valid JSON"):
            Pool.load(tmp_path / "pool.json")

    @pytest.mark.parametrize("data", [[], {}, {"accounts": []}, {"accounts": {"email": "x"}}])
    def test_needs_a_list_of_accounts(self, tmp_path, data):
        (tmp_path / "pool.json").write_text(json.dumps(data))

        with pytest.raises(PoolError, match=r'needs \{"accounts": \[\.\.\.\]\} with at least one account$'):
            Pool.load(tmp_path / "pool.json")

    def test_needs_text_fields(self, tmp_path):
        (tmp_path / "pool.json").write_text(json.dumps({"accounts": [{"email": "a"}, {"pin": 1234}]}))

        with pytest.raises(PoolError, match="account 2 must be an object of text fields$"):
            Pool.load(tmp_path / "pool.json")


class TestLease:
    def test_never_leases_one_account_twice(self):
        pool = pool_of("a", "b", "c")
        rng = random.Random(1)

        leased = {pool.lease(rng).index for _ in range(3)}

        assert leased == {1, 2, 3}
        assert pool.lease(rng) is None
        assert pool.free() == 0

    def test_a_released_account_can_be_leased_again(self):
        pool = pool_of("a")
        rng = random.Random(1)
        account = pool.lease(rng)

        pool.release(account)

        assert pool.lease(rng) == account

    def test_other_is_never_the_excluded_account(self):
        pool = pool_of("a", "b")
        rng = random.Random(1)
        user = pool.accounts[0]

        assert {pool.other(rng, user).index for _ in range(50)} == {2}

    def test_other_may_be_leased_by_someone_else(self):
        pool = pool_of("a", "b")
        rng = random.Random(1)
        pool.lease(rng)
        pool.lease(rng)

        assert pool.other(rng, pool.accounts[0]).index == 2

    def test_other_is_none_with_one_account(self):
        pool = pool_of("a")

        assert pool.other(random.Random(1), pool.accounts[0]) is None


class TestShard:
    def test_takes_every_nth_account(self):
        pool = pool_of("a", "b", "c", "d", "e")

        assert [a.fields["email"] for a in pool.shard(2, 2).accounts] == ["b", "d"]
        assert [a.fields["email"] for a in pool.shard(1, 2).accounts] == ["a", "c", "e"]

    def test_rejects_an_index_out_of_range(self):
        with pytest.raises(PoolError, match=r"^pool shard 3/2: the index must be from 1 to 2$"):
            pool_of("a", "b").shard(3, 2)

    def test_rejects_a_shard_with_no_accounts(self):
        with pytest.raises(PoolError, match=r"^pool shard 3/3: no accounts left for this shard$"):
            pool_of("a", "b").shard(3, 3)


class TestCheckPool:
    def test_lists_the_fields_a_graph_uses(self, graph):
        graph["nodes"]["logout"]["body"] = {"to": "{pool:other_user.email}"}

        assert pool_fields(parse_graph(json.dumps(graph))) == {
            "user": {"email", "password"},
            "other_user": {"email"},
        }

    def test_accepts_a_pool_with_every_field(self, graph):
        assert check_pool(parse_graph(json.dumps(graph)), pool_of("a")) == []

    def test_needs_a_pool_when_the_graph_uses_one(self, graph):
        assert check_pool(parse_graph(json.dumps(graph)), None) == [
            "the graph uses pool accounts (user.email, user.password) but there is no pool file"
        ]

    def test_needs_no_pool_when_the_graph_uses_none(self, graph):
        del graph["nodes"]["login"]["body"]

        assert check_pool(parse_graph(json.dumps(graph)), None) == []

    def test_reports_accounts_missing_a_field(self, graph):
        pool = Pool([{"email": "a", "password": "x"}, {"email": "b"}])

        assert check_pool(parse_graph(json.dumps(graph)), pool) == ["account 2 has no password"]

    def test_needs_two_accounts_for_other_user(self, graph):
        graph["nodes"]["logout"]["body"] = {"to": "{pool:other_user.email}"}

        assert check_pool(parse_graph(json.dumps(graph)), pool_of("a")) == [
            "the graph uses {pool:other_user.…}, which needs at least 2 accounts"
        ]

    def test_rejects_a_role_the_pool_does_not_provide(self, graph):
        graph["nodes"]["logout"]["body"] = {"admin": "{pool:admin.email}"}

        assert check_pool(parse_graph(json.dumps(graph)), pool_of("a")) == [
            "the graph uses {pool:admin.…}, but the pool only provides user and other_user"
        ]
