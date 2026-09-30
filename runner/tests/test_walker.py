import json
import random
import re
from datetime import datetime, timezone

import pytest

from murmur_runner.graph import EXIT, Extract, parse_graph
from murmur_runner.walker import PlaceholderError, Session, Step, extract_values, pick_persona

NOW = datetime(2026, 9, 29, 18, 5, 7, 123000, tzinfo=timezone.utc)
POOL = {
    "user": {"email": "pool1@test.com", "password": "hunter22", "phone": "+10005550001"},
    "other_user": {"email": "pool2@test.com"},
}
ENV = {"MURMUR_KEY": "key-123"}


def load(data):
    return parse_graph(json.dumps(data))


def session_for(data, persona="browser", seed=1, **kwargs):
    graph = load(data)
    options = {"pool": POOL, "env": ENV, "now": lambda: NOW} | kwargs
    return Session(graph, graph.personas[persona], random.Random(seed), **options)


def at(session, node, flags=(), values=None):
    session.node = node
    session.flags = set(flags)
    session.values = dict(values or {})
    return session


class TestPickPersona:
    def test_never_picks_a_persona_with_no_share(self, graph):
        graph["personas"] = {"browser": {"share": 1}, "buyer": {"share": 0}}

        picked = {pick_persona(load(graph), random.Random(seed)).name for seed in range(200)}

        assert picked == {"browser"}

    def test_picks_by_share(self, graph):
        rng = random.Random(3)
        loaded = load(graph)

        picks = [pick_persona(loaded, rng).name for _ in range(10000)]

        assert 0.57 < picks.count("browser") / len(picks) < 0.63


class TestChoices:
    def test_starts_at_the_start_node(self, graph):
        assert session_for(graph).choices() == {"home": 1.0}

    def test_ends_when_the_start_node_cannot_be_entered(self, graph):
        graph["nodes"]["home"]["requires"] = ["authed"]

        session = session_for(graph)

        assert session.choices() == {}
        assert session.next() == EXIT

    def test_drops_edges_whose_target_needs_a_missing_flag_and_rescales(self, graph):
        graph["personas"]["browser"]["multipliers"] = {}
        session = at(session_for(graph), "search", flags={"authed"})

        # add_to_cart also needs has_results, so it drops out: 0.2 + 0.3 + 0.2 remain.
        assert session.choices() == pytest.approx({"search": 0.2 / 0.7, "home": 0.3 / 0.7, "exit": 0.2 / 0.7})

    def test_drops_edges_whose_target_forbids_a_set_flag(self, graph):
        graph["personas"]["browser"]["multipliers"] = {}
        session = at(session_for(graph), "home", flags={"authed"})

        # login and signup require_not authed.
        assert session.choices() == pytest.approx({"search": 0.5 / 0.7, "exit": 0.2 / 0.7})

    def test_applies_the_persona_multipliers(self, graph):
        session = at(session_for(graph, persona="browser"), "home")

        # browse x1.5: search 0.75, login 0.2, signup 0.1, exit 0.2, total 1.25.
        assert session.choices() == pytest.approx(
            {"search": 0.75 / 1.25, "login": 0.2 / 1.25, "signup": 0.1 / 1.25, "exit": 0.2 / 1.25}
        )

    def test_merges_two_edges_to_the_same_node(self, graph):
        graph["personas"]["browser"]["multipliers"] = {}
        graph["edges"]["signup"] = [
            {"to": "search", "p": 0.3, "tag": "browse"},
            {"to": "search", "p": 0.4, "tag": "account"},
            {"to": "exit", "p": 0.3, "tag": "exit"},
        ]
        session = at(session_for(graph), "signup")

        assert session.choices() == pytest.approx({"search": 0.7, "exit": 0.3})

    def test_ends_when_every_weight_is_zero(self, graph):
        graph["personas"]["browser"]["multipliers"] = {"exit": 0}
        session = at(session_for(graph), "logout")

        assert session.choices() == {}
        assert session.next() == EXIT

    def test_draws_from_the_choices(self, graph):
        session = at(session_for(graph), "logout")

        assert session.next() == EXIT


class TestPrepare:
    def test_fills_in_a_value_extracted_earlier(self, graph):
        session = at(session_for(graph), "search", flags={"authed", "has_results"}, values={"productId": "p-9"})

        assert session.prepare("add_to_cart").path == "/cart/p-9"

    def test_keeps_the_type_of_a_value_that_is_the_whole_string(self, graph):
        graph["nodes"]["add_to_cart"]["body"] = {"productId": "{productId}", "note": "id {productId}"}
        session = at(session_for(graph), "search", values={"productId": 42})

        assert session.prepare("add_to_cart").body == {"productId": 42, "note": "id 42"}

    def test_fills_in_object_keys(self, graph):
        graph["nodes"]["add_to_cart"]["body"] = {"cart": {"{productId}": 1}}
        session = at(session_for(graph), "search", values={"productId": 42})

        assert session.prepare("add_to_cart").body == {"cart": {"42": 1}}

    def test_fills_in_extract_paths(self, graph):
        graph["nodes"]["add_to_cart"]["extract"] = {
            "lineId": {"path": "$.lines[?(@.productId == '{productId}')].id", "pick": "first", "required": True}
        }
        session = at(session_for(graph), "search", values={"productId": "p-9"})

        assert session.prepare("add_to_cart").extracts == {
            "lineId": Extract("$.lines[?(@.productId == 'p-9')].id", "first", True)
        }

    def test_generates_test_rule_values_from_the_pattern(self, graph):
        graph["test_rules"]["test_phone"] = {
            "description": "test phones", "generate": "+1000#######", "source": "x", "dev_only": True
        }
        graph["nodes"]["signup"]["body"]["phone"] = "{test:test_phone}"

        body = session_for(graph).prepare("signup").body

        assert re.fullmatch(r"[a-z]{12}@test\.com", body["email"])
        assert re.fullmatch(r"\+1000[0-9]{7}", body["phone"])
        assert body["otp"] == "000000"

    def test_uses_one_value_per_placeholder_within_a_step(self, graph):
        graph["nodes"]["signup"]["body"]["confirmEmail"] = "{test:test_email}"

        body = session_for(graph).prepare("signup").body

        assert body["confirmEmail"] == body["email"]

    def test_generated_test_values_differ_even_with_the_same_seed(self, graph):
        first = session_for(graph, seed=7).prepare("signup").body["email"]

        assert session_for(graph, seed=7).prepare("signup").body["email"] != first

    def test_generates_a_fresh_value_in_the_next_step(self, graph):
        session = session_for(graph)

        assert session.prepare("signup").body["email"] != session.prepare("signup").body["email"]

    def test_fills_in_pool_accounts(self, graph):
        body = session_for(graph).prepare("login").body

        assert body == {"email": "pool1@test.com", "password": "hunter22"}

    def test_fills_in_generators(self, graph):
        graph["nodes"]["home"]["request"] = "GET /events?from={gen:now_iso}"

        assert session_for(graph).prepare("home").path == "/events?from=2026-09-29T18:05:07.123Z"

    def test_sends_the_skip_in_place_of_the_node(self, graph):
        step = session_for(graph).prepare("checkout")

        assert step == Step(
            node="checkout",
            method="POST",
            path="/internal/murmur/complete-order",
            headers={"X-Murmur-Key": "key-123"},
            body=None,
            extracts={"orderId": Extract("$.order.id", "first", True)},
            sets=(),
            clears=("has_cart",),
            via_skip=True,
        )

    def test_leaves_out_a_top_level_header_until_its_value_exists(self, graph):
        session = session_for(graph)

        assert session.prepare("home").headers == {}

    def test_sends_a_top_level_header_once_its_value_exists(self, graph):
        session = at(session_for(graph), "login", values={"token": "Bearer abc"})

        assert session.prepare("home").headers == {"Authorization": "Bearer abc"}

    def test_sends_top_level_headers_to_skips_too(self, graph):
        session = at(session_for(graph), "add_to_cart", values={"token": "Bearer abc"})

        assert session.prepare("checkout").headers == {"Authorization": "Bearer abc", "X-Murmur-Key": "key-123"}

    def test_a_skip_header_wins_over_a_top_level_header_in_any_case(self, graph):
        graph["headers"]["x-murmur-key"] = "from-graph"
        session = session_for(graph)

        assert session.prepare("checkout").headers == {"X-Murmur-Key": "key-123"}

    def test_rejects_a_value_not_extracted_yet(self, graph):
        with pytest.raises(PlaceholderError, match=r"^\{productId\} has not been extracted$"):
            session_for(graph).prepare("add_to_cart")

    def test_rejects_an_unset_environment_variable(self, graph):
        with pytest.raises(PlaceholderError, match=r"^\{env:MURMUR_KEY\}: MURMUR_KEY is not set$"):
            session_for(graph, env={}).prepare("checkout")

    def test_rejects_a_pool_account_the_session_lacks(self, graph):
        with pytest.raises(PlaceholderError, match=r"^\{pool:user\.email\}: this session has no user account$"):
            session_for(graph, pool={}).prepare("login")

    def test_rejects_a_pool_field_the_account_lacks(self, graph):
        with pytest.raises(PlaceholderError, match=r"^\{pool:user\.email\}: the user account has no email$"):
            session_for(graph, pool={"user": {"password": "x"}}).prepare("login")


class TestComplete:
    def test_stores_values_and_sets_flags(self, graph):
        session = session_for(graph)
        step = session.prepare("search")

        assert session.complete(step, {"productId": "p-1"}) is True
        assert session.node == "search"
        assert session.values == {"productId": "p-1"}
        assert session.flags == {"has_results"}

    def test_clears_before_it_sets(self, graph):
        graph["nodes"]["login"]["clears"] = ["authed"]
        session = at(session_for(graph), "home")

        session.complete(session.prepare("login"), {"token": "Bearer abc"})

        assert session.flags == {"authed"}

    def test_clearing_session_clears_every_session_flag(self, graph):
        session = at(session_for(graph), "checkout", flags={"authed", "has_cart", "has_results"})

        session.complete(session.prepare("logout"), {})

        assert session.flags == {"has_results"}

    def test_a_missing_required_extract_sets_no_flags_and_forgets_the_old_value(self, graph):
        session = at(session_for(graph), "home", flags={"authed"}, values={"productId": "old"})

        assert session.complete(session.prepare("search"), {}) is False
        assert session.node == "search"
        assert session.flags == {"authed"}
        assert session.values == {}

    def test_a_failed_request_changes_nothing(self, graph):
        session = at(session_for(graph), "home")

        assert session.complete(session.prepare("login"), None) is False
        assert session.node == "login"
        assert session.flags == set()

    def test_forgets_an_optional_extract_that_found_nothing(self, graph):
        graph["nodes"]["search"]["extract"]["productId"]["required"] = False
        session = at(session_for(graph), "home", values={"productId": "old"})

        assert session.complete(session.prepare("search"), {}) is True
        assert session.values == {}

    def test_fail_moves_to_the_node_without_changing_state(self, graph):
        session = at(session_for(graph), "home", flags={"authed"})

        session.fail("add_to_cart")

        assert session.node == "add_to_cart"
        assert session.flags == {"authed"}


class TestExtractValues:
    BODY = {
        "items": [
            {"id": 1, "visible": True, "kind": "vip"},
            {"id": 2, "visible": True, "kind": "general"},
            {"id": 3, "visible": False, "kind": "general"},
        ]
    }

    def step(self, **extracts):
        return Step("n", "GET", "/", {}, None, extracts, (), (), False)

    def test_first_takes_the_first_match(self):
        step = self.step(itemId=Extract("$.items[*].id", "first", True))

        assert extract_values(step, self.BODY, random.Random(1)) == {"itemId": 1}

    def test_random_spreads_across_matches(self):
        step = self.step(itemId=Extract("$.items[*].id", "random", True))
        rng = random.Random(1)

        assert {extract_values(step, self.BODY, rng)["itemId"] for _ in range(100)} == {1, 2, 3}

    def test_applies_filters(self):
        step = self.step(itemId=Extract("$.items[?(@.visible == true && @.kind != 'vip')].id", "random", True))

        assert extract_values(step, self.BODY, random.Random(1)) == {"itemId": 2}

    def test_reads_a_header_in_any_case(self):
        step = self.step(token=Extract(None, "first", True, "Authorization"))

        found = extract_values(step, None, random.Random(1), {"authorization": "Bearer abc"})

        assert found == {"token": "Bearer abc"}

    def test_leaves_out_a_missing_header(self):
        step = self.step(token=Extract(None, "first", True, "Authorization"))

        assert extract_values(step, self.BODY, random.Random(1), {"Content-Type": "application/json"}) == {}

    def test_leaves_out_a_path_when_there_is_no_body(self):
        step = self.step(itemId=Extract("$.items[*].id", "first", True))

        assert extract_values(step, None, random.Random(1)) == {}

    def test_leaves_out_a_path_that_matches_nothing(self):
        step = self.step(itemId=Extract("$.orders[*].id", "first", True))

        assert extract_values(step, self.BODY, random.Random(1)) == {}


class TestPersonaFlags:
    def test_a_session_starts_with_its_persona_flags(self, graph):
        graph["personas"]["buyer"]["flags"] = ["vip"]
        graph["nodes"]["logout"]["requires"] = ["vip"]

        assert session_for(graph, persona="buyer").flags == {"vip"}

    def test_pick_persona_skips_personas_with_a_fixed_count(self, graph):
        graph["personas"] = {"staff": {"count": 5}, "buyer": {"share": 1}}

        picked = {pick_persona(load(graph), random.Random(seed)).name for seed in range(50)}

        assert picked == {"buyer"}


def with_check(graph, **extract):
    graph["nodes"]["search"]["extract"]["newest"] = {"path": "$.items[0].id", "pick": "first", **extract}
    return graph


class TestUnlocksAndLocks:
    def test_unlocks_a_flag_when_the_check_finds_a_value(self, graph):
        session = session_for(with_check(graph, unlocks=["has_items"]))

        session.complete(session.prepare("search"), {"productId": "p1", "newest": "p9"})

        assert "has_items" in session.flags

    def test_locks_it_again_when_the_check_finds_nothing(self, graph):
        session = at(session_for(with_check(graph, unlocks=["has_items"])), "home", flags={"has_items"})

        session.complete(session.prepare("search"), {"productId": "p1"})

        assert "has_items" not in session.flags

    def test_locks_clear_a_flag_when_the_check_finds_a_value(self, graph):
        graph["nodes"]["home"]["sets"] = ["busy"]
        session = at(session_for(with_check(graph, locks=["busy"])), "home", flags={"busy"})

        session.complete(session.prepare("search"), {"productId": "p1", "newest": "p9"})

        assert "busy" not in session.flags

    def test_locks_leave_the_flag_when_the_check_finds_nothing(self, graph):
        graph["nodes"]["home"]["sets"] = ["busy"]
        session = at(session_for(with_check(graph, locks=["busy"])), "home", flags={"busy"})

        session.complete(session.prepare("search"), {"productId": "p1"})

        assert "busy" in session.flags

    def test_checks_apply_even_when_a_required_value_is_missing(self, graph):
        session = session_for(with_check(graph, unlocks=["has_items"]))

        assert session.complete(session.prepare("search"), {"newest": "p9"}) is False
        assert session.flags == {"has_items"}  # has_results, the step's own flag, is not set

    def test_a_failed_request_changes_no_checks(self, graph):
        session = at(session_for(with_check(graph, unlocks=["has_items"])), "home", flags={"has_items"})

        session.complete(session.prepare("search"), None)

        assert session.flags == {"has_items"}


def with_board(graph):
    graph["nodes"]["checkout"]["skip"]["post"] = {"orders": "order {orderId}"}
    graph["nodes"]["logout"]["body"] = {"order": "{board:orders}"}
    graph["nodes"]["logout"]["requires"] = []
    return graph


class TestBoard:
    def test_a_node_that_reads_an_empty_board_is_locked(self, graph):
        session = session_for(with_board(graph))

        assert session.can_enter("logout") is False

    def test_a_successful_step_posts_to_the_board(self, graph):
        session = at(session_for(with_board(graph)), "add_to_cart", flags={"has_cart"})

        session.complete(session.prepare("checkout"), {"orderId": "o7"})

        assert session.board.count("orders") == 1
        assert session.can_enter("logout") is True

    def test_a_step_that_found_nothing_posts_nothing(self, graph):
        session = at(session_for(with_board(graph)), "add_to_cart", flags={"has_cart"})

        session.complete(session.prepare("checkout"), {})

        assert session.board.count("orders") == 0

    def test_taking_a_value_removes_it_for_every_other_session(self, graph):
        first = session_for(with_board(graph))
        first.board.post("orders", "order o7")
        second = session_for(graph, board=first.board)

        assert second.prepare("logout").body == {"order": "order o7"}
        assert first.can_enter("logout") is False

    def test_a_step_that_cannot_be_prepared_gives_the_value_back(self, graph):
        with_board(graph)["nodes"]["logout"]["body"]["product"] = "{productId}"
        session = session_for(graph)
        session.board.post("orders", "order o7")

        with pytest.raises(PlaceholderError):
            session.prepare("logout")

        assert session.board.take("orders") == "order o7"


def with_account(graph, ready="authed"):
    graph["nodes"]["signup"]["body"]["password"] = "{gen:password}"
    graph["nodes"]["signup"]["account"] = {"group": "customer", "fields": {"email": "{test:test_email}", "password": "{gen:password}"}}
    if ready:
        graph["nodes"]["signup"]["account"]["ready"] = ready
    return graph


class TestAccounts:
    def test_the_new_account_holds_the_values_the_step_sent(self, graph):
        session = session_for(with_account(graph))
        step = session.prepare("signup")

        session.complete(step, {})

        assert session.own["user"] == {"email": step.body["email"], "password": step.body["password"]}
        assert POOL["user"]["email"] == "pool1@test.com"  # the accounts it was given are unchanged

    def test_it_becomes_usable_once_the_ready_flag_is_set(self, graph):
        with_account(graph, ready="verified")
        graph["nodes"]["login"]["sets"] = ["authed", "verified"]
        session = session_for(graph)
        session.complete(session.prepare("signup"), {})

        assert session.take_ready_accounts() == []

        session.complete(session.prepare("login"), {"token": "t"})

        assert [groups for groups, _ in session.take_ready_accounts()] == [("customer",)]
        assert session.take_ready_accounts() == []

    def test_it_is_usable_at_once_without_a_ready_flag(self, graph):
        session = session_for(with_account(graph, ready=None))

        session.complete(session.prepare("signup"), {})

        assert len(session.take_ready_accounts()) == 1

    def test_a_failed_signup_creates_nothing(self, graph):
        session = session_for(with_account(graph))

        session.complete(session.prepare("signup"), None)

        assert session.own == {} and session.take_ready_accounts() == []


class TestUserFlag:
    def test_a_session_with_an_account_has_user(self, graph):
        graph["nodes"]["login"]["requires"] = ["@user"]

        assert session_for(graph).can_enter("login") is True
        assert session_for(graph, pool={}).can_enter("login") is False

    def test_creating_an_account_gives_the_session_user(self, graph):
        with_account(graph)
        graph["nodes"]["logout"]["requires"] = ["@user"]
        session = session_for(graph, pool={})

        session.complete(session.prepare("signup"), {})

        assert session.can_enter("logout") is True


class TestJoins:
    def test_a_created_account_joins_the_groups_when_it_is_ready(self, graph):
        with_account(graph, ready="verified")
        graph["nodes"]["login"]["sets"] = ["authed", "verified"]
        graph["nodes"]["search"]["joins"] = ["owner"]
        session = session_for(graph)
        session.complete(session.prepare("signup"), {})
        session.complete(session.prepare("search"), {"productId": "p1"})
        session.complete(session.prepare("login"), {"token": "t"})

        assert [groups for groups, _ in session.take_ready_accounts()] == [("customer", "owner")]
        assert session.take_joins() == []

    def test_a_leased_account_joins_through_take_joins(self, graph):
        graph["nodes"]["search"]["joins"] = ["owner"]
        session = session_for(graph)

        session.complete(session.prepare("search"), {"productId": "p1"})

        assert session.take_joins() == ["owner"]
        assert session.take_joins() == []

    def test_a_step_that_found_nothing_joins_nothing(self, graph):
        graph["nodes"]["search"]["joins"] = ["owner"]
        session = session_for(graph)

        session.complete(session.prepare("search"), {})

        assert session.take_joins() == []

    def test_a_session_without_an_account_notes_the_join(self, graph):
        graph["nodes"]["search"]["joins"] = ["owner"]
        session = session_for(graph, pool={})

        session.complete(session.prepare("search"), {"productId": "p1"})

        assert session.problems == ["search: no account to join owner"]



class TestSkipOutcomes:
    def test_a_node_level_join_applies_when_a_skip_sends_the_request(self, graph):
        graph["nodes"]["checkout"]["joins"] = ["owner"]
        session = at(session_for(graph), "add_to_cart", flags={"has_cart"})

        session.complete(session.prepare("checkout"), {"orderId": "o1"})

        assert session.take_joins() == ["owner"]

    def test_the_skip_s_own_join_wins(self, graph):
        graph["nodes"]["checkout"]["joins"] = ["owner"]
        graph["nodes"]["checkout"]["skip"]["joins"] = ["staff"]
        session = at(session_for(graph), "add_to_cart", flags={"has_cart"})

        session.complete(session.prepare("checkout"), {"orderId": "o1"})

        assert session.take_joins() == ["staff"]


class TestBoardOnFailure:
    def test_a_failed_request_gives_the_board_value_back(self, graph):
        session = session_for(with_board(graph))
        session.board.post("orders", "order o7")

        session.complete(session.prepare("logout"), None)

        assert session.board.take("orders") == "order o7"

    def test_a_request_that_worked_keeps_the_value_taken(self, graph):
        session = session_for(with_board(graph))
        session.board.post("orders", "order o7")

        session.complete(session.prepare("logout"), {})

        assert session.board.count("orders") == 0


class TestGroupConditions:
    def test_a_leased_account_is_in_its_groups(self, graph):
        graph["personas"]["staff"] = {"count": 1, "pool": "staff"}
        graph["nodes"]["logout"]["requires"] = ["@in:staff"]

        assert session_for(graph, groups={"staff"}).can_enter("logout") is True
        assert session_for(graph, groups={"default"}).can_enter("logout") is False

    def test_joining_a_group_opens_its_nodes(self, graph):
        graph["personas"]["staff"] = {"count": 1, "pool": "staff"}
        graph["nodes"]["search"]["joins"] = ["staff"]
        graph["nodes"]["logout"]["requires"] = ["@in:staff"]
        session = session_for(graph, groups={"default"})

        session.complete(session.prepare("search"), {"productId": "p1"})

        assert session.can_enter("logout") is True

    def test_a_created_account_is_in_its_group_and_the_groups_it_joins(self, graph):
        with_account(graph)
        graph["personas"]["staff"] = {"count": 1, "pool": "staff"}
        graph["nodes"]["search"]["joins"] = ["staff"]
        graph["nodes"]["logout"]["requires"] = ["@in:staff"]
        session = session_for(graph, pool={})
        session.complete(session.prepare("signup"), {})

        assert session.groups() == {"customer"}

        session.complete(session.prepare("search"), {"productId": "p1"})

        assert session.groups() == {"customer", "staff"} and session.can_enter("logout") is True

    def test_a_join_after_the_account_is_ready_reaches_the_pool(self, graph):
        with_account(graph, ready=None)
        graph["nodes"]["search"]["joins"] = ["staff"]
        session = session_for(graph, pool={})
        session.complete(session.prepare("signup"), {})
        session.take_ready_accounts()

        session.complete(session.prepare("search"), {"productId": "p1"})

        assert [set(groups) for groups, _ in session.take_ready_accounts()] == [{"customer", "staff"}]

    def test_a_session_without_an_account_is_in_no_group(self, graph):
        assert session_for(graph, pool={}).groups() == set()
