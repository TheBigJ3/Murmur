import json
import random

import pytest

from murmur_runner.graph import parse_graph
from murmur_runner.pool import Pool, PoolError, check_pool, drop_incomplete_grown, grown_name, growable_groups, pool_fields, pool_notes


def pool_of(*emails, group=None):
    accounts = [{"email": e, "password": "pw"} for e in emails]
    return Pool(groups={group: accounts}) if group else Pool(accounts)


def write(tmp_path, data, name="pool.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


class TestLoad:
    def test_loads_the_default_group(self, pool_file):
        pool = Pool.load(pool_file)

        assert [(a.index, a.fields["email"], a.groups) for a in pool.accounts] == [
            (1, "pool1@test.com", {"default"}),
            (2, "pool2@test.com", {"default"}),
        ]

    def test_an_account_listed_in_two_groups_is_one_account(self, tmp_path):
        owner = {"email": "o", "password": "x"}
        path = write(tmp_path, {"accounts": [owner], "groups": {"owner": [owner]}})

        pool = Pool.load(path)

        assert [(a.index, a.groups) for a in pool.accounts] == [(1, {"default", "owner"})]
        pool.lease(random.Random(1), "owner")
        assert pool.lease(random.Random(1), "default") is None

    def test_loads_named_groups(self, tmp_path):
        path = write(tmp_path, {"accounts": [{"email": "a"}], "groups": {"staff": [{"email": "s1"}, {"email": "s2"}]}})

        pool = Pool.load(path)

        assert [a.fields["email"] for a in pool.group("default")] == ["a"]
        assert [a.fields["email"] for a in pool.group("staff")] == ["s1", "s2"]

    def test_adds_the_saved_grown_accounts(self, tmp_path, pool_file):
        grown = write(tmp_path, {"groups": {"default": [{"email": "new@test.com", "password": "x"}]}}, "pool.grown.json")

        pool = Pool.load(pool_file, grown)

        assert [(a.fields["email"], a.grown) for a in pool.accounts] == [
            ("pool1@test.com", False), ("pool2@test.com", False), ("new@test.com", True),
        ]

    def test_works_without_a_pool_file_or_saved_accounts(self, tmp_path):
        assert Pool.load(None, tmp_path / "pool.grown.json").accounts == []

    def test_reports_a_missing_file(self, tmp_path):
        with pytest.raises(PoolError, match="cannot read the file: No such file or directory$"):
            Pool.load(tmp_path / "pool.json")

    def test_reports_invalid_json(self, tmp_path):
        (tmp_path / "pool.json").write_text("{accounts: []}")

        with pytest.raises(PoolError, match="not valid JSON"):
            Pool.load(tmp_path / "pool.json")

    @pytest.mark.parametrize("data", [[], {}, {"users": []}])
    def test_needs_accounts_or_groups(self, tmp_path, data):
        with pytest.raises(PoolError, match=r'needs \{"accounts": \[\.\.\.\]\} or \{"groups": \{"<name>": \[\.\.\.\]\}\}$'):
            Pool.load(write(tmp_path, data))

    def test_needs_at_least_one_account(self, tmp_path):
        with pytest.raises(PoolError, match="has no accounts$"):
            Pool.load(write(tmp_path, {"accounts": []}))

    def test_needs_each_group_to_be_a_list(self, tmp_path):
        with pytest.raises(PoolError, match="accounts must be a list of accounts$"):
            Pool.load(write(tmp_path, {"accounts": {"email": "x"}}))

    def test_names_a_group_that_is_not_a_list(self, tmp_path):
        with pytest.raises(PoolError, match="groups.staff must be a list of accounts$"):
            Pool.load(write(tmp_path, {"groups": {"staff": None}}))

    def test_rejects_an_unknown_key(self, tmp_path):
        with pytest.raises(PoolError, match="unknown key grups; use accounts or groups$"):
            Pool.load(write(tmp_path, {"accounts": [{"email": "a"}], "grups": {}}))

    def test_needs_text_fields(self, tmp_path):
        with pytest.raises(PoolError, match=r"accounts\[1\] must be an object of text fields$"):
            Pool.load(write(tmp_path, {"accounts": [{"email": "a"}, {"pin": 1234}]}))


class TestLease:
    def test_never_leases_one_account_twice(self):
        pool = pool_of("a", "b", "c")
        rng = random.Random(1)

        assert {pool.lease(rng).index for _ in range(3)} == {1, 2, 3}
        assert pool.lease(rng) is None
        assert pool.free() == 0

    def test_leases_only_from_the_given_group(self):
        pool = Pool([{"email": "a"}], groups={"staff": [{"email": "s"}]})
        rng = random.Random(1)

        assert pool.lease(rng, "staff").fields == {"email": "s"}
        assert pool.lease(rng, "staff") is None
        assert pool.free("default") == 1

    def test_a_released_account_can_be_leased_again(self):
        pool = pool_of("a")
        rng = random.Random(1)
        account = pool.lease(rng)

        pool.release(account)

        assert pool.lease(rng) == account

    def test_other_is_another_account_of_the_same_group(self):
        pool = Pool([{"email": "a"}, {"email": "b"}], groups={"staff": [{"email": "s"}]})
        rng = random.Random(1)

        assert {pool.other(rng, pool.accounts[0]).fields["email"] for _ in range(50)} == {"b"}

    def test_other_may_be_leased_by_someone_else(self):
        pool = pool_of("a", "b")
        rng = random.Random(1)
        pool.lease(rng)
        pool.lease(rng)

        assert pool.other(rng, pool.accounts[0]).index == 2

    def test_other_is_none_with_one_account(self):
        pool = pool_of("a")

        assert pool.other(random.Random(1), pool.accounts[0]) is None


class TestAdd:
    def test_adds_an_account_leased_to_its_session(self):
        pool = pool_of("a")

        account = pool.add("default", {"email": "new", "password": "x"}, leased=True)

        assert (account.index, account.grown) == (2, True)
        assert pool.free() == 1

    def test_saves_it_with_the_grown_accounts(self, tmp_path):
        grown = tmp_path / "pool.grown.json"
        pool = Pool.load(None, grown)

        pool.add("customer", {"email": "new", "password": "x"})

        assert json.loads(grown.read_text()) == {"groups": {"customer": [{"email": "new", "password": "x"}]}}

    def test_keeps_accounts_another_process_saved(self, tmp_path):
        grown = write(tmp_path, {"groups": {"default": [{"email": "theirs"}]}}, "pool.grown.json")
        pool = Pool(grown_path=grown)  # loaded before the other process saved

        pool.add("default", {"email": "mine"})

        assert json.loads(grown.read_text()) == {"groups": {"default": [{"email": "theirs"}, {"email": "mine"}]}}

    def test_saves_nothing_without_a_grown_file(self, tmp_path):
        pool = pool_of("a")

        pool.add("default", {"email": "new"})

        assert list(tmp_path.iterdir()) == []

    def test_turns_values_into_text(self):
        assert pool_of("a").add("default", {"id": 42}).fields == {"id": "42"}


class TestJoin:
    def test_adds_groups_and_saves_them(self, tmp_path):
        grown = tmp_path / "pool.grown.json"
        pool = Pool([{"email": "a", "password": "x"}], grown_path=grown)
        account = pool.accounts[0]

        assert pool.join(account, ["owner", "default"]) == ["owner"]
        assert account.groups == {"default", "owner"}
        assert json.loads(grown.read_text()) == {"groups": {"owner": [{"email": "a", "password": "x"}]}}

    def test_a_joined_file_account_keeps_its_group_in_the_next_run(self, tmp_path, pool_file):
        grown = tmp_path / "pool.grown.json"
        pool = Pool.load(pool_file, grown)
        pool.join(pool.accounts[0], "owner")

        again = Pool.load(pool_file, grown)

        assert [(a.fields["email"], a.groups, a.grown) for a in again.accounts] == [
            ("pool1@test.com", {"default", "owner"}, False),
            ("pool2@test.com", {"default"}, False),
        ]

    def test_a_created_account_can_join_several_groups(self):
        account = pool_of("a").add(("customer", "owner"), {"email": "new"})

        assert account.groups == {"customer", "owner"}


class TestShard:
    def test_every_file_account_is_in_exactly_one_shard(self):
        pool = Pool([{"email": f"user{i}"} for i in range(40)])

        shards = [pool.shard(i, 3) for i in (1, 2, 3)]

        emails = [a.fields["email"] for shard in shards for a in shard.accounts]
        assert sorted(emails) == sorted(f"user{i}" for i in range(40))

    def test_an_account_gets_the_same_shard_whatever_the_order(self):
        accounts = [{"email": f"user{i}"} for i in range(20)]

        first = {a.fields["email"] for a in Pool(accounts).shard(2, 3).accounts}
        second = {a.fields["email"] for a in Pool(list(reversed(accounts))).shard(2, 3).accounts}

        assert first == second

    def test_splits_evenly(self):
        pool = Pool([{"email": f"user{i}"} for i in range(40)])

        assert sorted(len(pool.shard(i, 3).accounts) for i in (1, 2, 3)) == [13, 13, 14]

    def test_keeps_the_groups_of_its_accounts(self):
        pool = Pool([{"email": "b"}], groups={"staff": [{"email": "b"}]})

        (account,) = [a for i in (1, 2) for a in pool.shard(i, 2).accounts]

        assert account.groups == {"default", "staff"}

    def test_deals_out_the_accounts_of_other_shards_grown_files(self, tmp_path):
        other = write(tmp_path, {"groups": {"default": [{"email": f"g{i}"} for i in range(6)]}}, "pool.grown.json")

        shards = [Pool.load(None, tmp_path / f"pool.grown.{i}of2.json", [other]).shard(i, 2) for i in (1, 2)]

        assert [len(s.accounts) for s in shards] == [3, 3]

    def test_keeps_every_grown_account(self, tmp_path):
        grown = write(tmp_path, {"groups": {"default": [{"email": f"g{i}"} for i in range(6)]}}, "pool.grown.1of3.json")

        mine = Pool.load(None, grown).shard(1, 3)

        assert len(mine.accounts) == 6

    def test_names_a_grown_file_per_shard(self):
        assert (grown_name(), grown_name((2, 3))) == ("pool.grown.json", "pool.grown.2of3.json")

    def test_rejects_an_index_out_of_range(self):
        with pytest.raises(PoolError, match=r"^pool shard 3/2: the index must be from 1 to 2$"):
            pool_of("a", "b").shard(3, 2)

    def test_a_shard_may_have_no_accounts(self):
        assert [len(pool_of("a").shard(i, 3).accounts) for i in (1, 2, 3)] == [1, 0, 0]


def load(graph):
    return parse_graph(json.dumps(graph))


def with_signup_account(graph, group="default", fields=None):
    graph["nodes"]["signup"]["body"]["password"] = "{gen:password}"
    graph["nodes"]["signup"]["account"] = {
        "group": group,
        "fields": fields or {"email": "{test:test_email}", "password": "{gen:password}"},
        "ready": "authed",
    }
    return graph


class TestCheckPool:
    def test_lists_the_fields_a_graph_uses(self, graph):
        graph["nodes"]["logout"]["body"] = {"to": "{pool:other_user.email}"}

        assert pool_fields(load(graph)) == {"user": {"email", "password"}, "other_user": {"email"}}

    def test_lists_the_groups_steps_create_accounts_for(self, graph):
        assert growable_groups(load(with_signup_account(graph, "customer"))) == {"customer": ["signup"]}

    def test_accepts_a_pool_with_every_field(self, graph):
        assert check_pool(load(graph), pool_of("a")) == []

    def test_needs_accounts_when_no_step_creates_any(self, graph):
        assert check_pool(load(graph), None) == [
            "there is no pool file, and no step creates accounts for the pool group default"
        ]

    def test_needs_no_pool_file_when_a_step_creates_accounts(self, graph):
        assert check_pool(load(with_signup_account(graph)), Pool()) == []

    def test_needs_no_pool_when_the_graph_uses_none(self, graph):
        del graph["nodes"]["login"]["body"]

        assert check_pool(load(graph), None) == []

    def test_needs_accounts_in_each_persona_group(self, graph):
        graph["personas"]["buyer"]["pool"] = "staff"

        assert check_pool(load(graph), pool_of("a")) == [
            "the pool group staff has no accounts, and no step creates any"
        ]

    def test_reports_accounts_missing_a_field(self, graph):
        pool = Pool([{"email": "a", "password": "x"}, {"email": "b"}])

        assert check_pool(load(graph), pool) == ["account 2 (default) has no password"]

    def test_reports_created_accounts_missing_a_field(self, graph):
        with_signup_account(graph, fields={"email": "{test:test_email}"})

        assert check_pool(load(graph), Pool()) == [
            "nodes.signup creates accounts with no password, which the graph uses as {pool:…}"
        ]

    def test_needs_two_accounts_for_other_user(self, graph):
        graph["nodes"]["logout"]["body"] = {"to": "{pool:other_user.email}"}

        assert check_pool(load(graph), pool_of("a")) == [
            "the graph uses {pool:other_user.…}, which needs at least 2 accounts in default"
        ]



class TestReviewFixes:
    def test_accounts_and_a_default_group_are_merged(self, tmp_path):
        path = write(tmp_path, {"accounts": [{"email": "a"}], "groups": {"default": [{"email": "b"}]}})

        assert [a.fields["email"] for a in Pool.load(path).group("default")] == ["a", "b"]

    def test_notes_a_group_that_stays_empty_without_growing(self, graph):
        graph = with_signup_account(graph, "customer")
        graph["personas"]["buyer"]["pool"] = "customer"

        assert pool_notes(load(graph), Pool([{"email": "a", "password": "x"}]), grow=False) == [
            "the pool group customer has no accounts and --no-grow keeps new ones out, "
            "so buyer sessions only have the accounts they create themselves"
        ]

    def test_notes_a_fixed_count_persona_with_too_few_accounts(self, graph):
        graph["personas"]["staff"] = {"count": 3, "pool": "staff"}
        pool = Pool([{"email": "a", "password": "x"}], groups={"staff": [{"email": "s", "password": "y"}]})

        assert pool_notes(load(graph), pool, grow=True) == [
            "staff runs 3 users but the pool group staff has 1 accounts, so some of them run without one"
        ]

    def test_a_created_account_needs_the_fields_other_user_uses(self, graph):
        with_signup_account(graph)
        graph["nodes"]["logout"]["body"] = {"to": "{pool:other_user.phone}"}

        assert check_pool(load(graph), Pool()) == [
            "nodes.signup creates accounts with no phone, which the graph uses as {pool:…}"
        ]


class TestGrownFiles:
    def test_says_which_file_an_account_came_from(self, tmp_path, pool_file):
        grown = write(tmp_path, {"groups": {"default": [{"email": "g"}]}}, "pool.grown.json")

        pool = Pool.load(pool_file, grown)

        assert [a.describe() for a in pool.accounts] == [
            "account 1 (default) in pool.json", "account 2 (default) in pool.json", "account 3 (default) in pool.grown.json",
        ]

    def test_reads_other_grown_files_but_saves_to_its_own(self, tmp_path):
        other = write(tmp_path, {"groups": {"default": [{"email": "theirs"}]}}, "pool.grown.1of2.json")
        mine = tmp_path / "pool.grown.json"
        pool = Pool.load(None, mine, [other])

        pool.add("default", {"email": "mine"})

        assert [a.fields["email"] for a in pool.accounts] == ["theirs", "mine"]
        assert json.loads(other.read_text()) == {"groups": {"default": [{"email": "theirs"}]}}
        assert json.loads(mine.read_text()) == {"groups": {"default": [{"email": "mine"}]}}

    def test_leaves_out_grown_accounts_missing_a_field(self, graph, tmp_path, pool_file):
        grown = write(tmp_path, {"groups": {"default": [{"email": "old"}]}}, "pool.grown.json")
        pool = Pool.load(pool_file, grown)

        notes = drop_incomplete_grown(load(graph), pool)

        assert notes == ["left out 1 grown accounts in pool.grown.json that have no password; delete pool.grown.json to start growing afresh"]
        assert [a.fields["email"] for a in pool.accounts] == ["pool1@test.com", "pool2@test.com"]
        assert check_pool(load(graph), pool) == []

    def test_lists_an_incomplete_pool_file_account_once(self, graph):
        graph["personas"]["staff"] = {"count": 1}
        pool = Pool([{"email": "a"}])

        assert check_pool(load(graph), pool) == ["account 1 (default) has no password"]
