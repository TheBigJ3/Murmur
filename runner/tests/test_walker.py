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

    def test_rejects_a_value_not_extracted_yet(self, graph):
        with pytest.raises(PlaceholderError, match=r"^\{productId\} has not been extracted$"):
            session_for(graph).prepare("add_to_cart")

    def test_rejects_an_unset_environment_variable(self, graph):
        with pytest.raises(PlaceholderError, match=r"^\{env:MURMUR_KEY\}: MURMUR_KEY is not set$"):
            session_for(graph, env={}).prepare("checkout")

    def test_rejects_a_pool_field_the_accounts_lack(self, graph):
        with pytest.raises(PlaceholderError, match=r"^\{pool:user\.email\}: the leased accounts have no user\.email$"):
            session_for(graph, pool={}).prepare("login")


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

        session.complete(session.prepare("login"), {})

        assert session.flags == {"authed"}

    def test_clearing_session_clears_every_session_flag(self, graph):
        session = at(session_for(graph), "checkout", flags={"authed", "has_cart", "has_results"})

        session.complete(session.prepare("logout"), {})

        assert session.flags == {"has_results"}

    def test_a_missing_required_extract_fails_the_step_and_changes_nothing(self, graph):
        session = at(session_for(graph), "home", flags={"authed"}, values={"productId": "old"})

        assert session.complete(session.prepare("search"), {}) is False
        assert session.node == "search"
        assert session.flags == {"authed"}
        assert session.values == {"productId": "old"}

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

    def test_leaves_out_a_path_that_matches_nothing(self):
        step = self.step(itemId=Extract("$.orders[*].id", "first", True))

        assert extract_values(step, self.BODY, random.Random(1)) == {}
