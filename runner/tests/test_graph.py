import json

import pytest

from murmur_runner.generators import GENERATORS
from murmur_runner.graph import (
    Edge,
    Extract,
    GraphError,
    Persona,
    Problem,
    Request,
    TestRule,
    load_graph,
    parse_graph,
)


def errors_of(data) -> tuple[Problem, ...]:
    with pytest.raises(GraphError) as caught:
        parse_graph(json.dumps(data))
    return caught.value.errors


def warnings_of(data) -> tuple[Problem, ...]:
    return parse_graph(json.dumps(data)).warnings


class TestLoadGraph:
    def test_builds_the_graph_from_a_valid_file(self, graph, write_graph):
        loaded = load_graph(write_graph(graph))

        assert loaded.murmur_version == "0.2.0"
        assert loaded.start == "home"
        assert loaded.session_flags == ("authed", "has_cart")
        assert loaded.test_rules["test_otp"] == TestRule(
            "test_otp", "test phones accept a fixed OTP", None, "000000", "src/otp.ts:verify", True
        )
        assert loaded.nodes["search"].request == Request("GET", "/products?q={gen:query}")
        assert loaded.nodes["search"].extract == {"productId": Extract("$.items[*].id", "random", True)}
        assert loaded.nodes["logout"].clears == ("@session",)
        assert loaded.nodes["checkout"].skip.request == Request("POST", "/internal/murmur/complete-order")
        assert loaded.nodes["checkout"].skip.clears == ("has_cart",)
        assert loaded.nodes["home"].skip is None
        assert loaded.edges["logout"] == (Edge("exit", 1.0, "exit"),)
        assert loaded.personas["buyer"] == Persona("buyer", 0.4, {"purchase": 2.0, "exit": 0.5})
        assert loaded.headers == {"Authorization": "{token}"}
        assert loaded.nodes["login"].extract == {"token": Extract(None, "first", True, "Authorization")}
        assert loaded.warnings == ()

    def test_reports_a_missing_file(self, tmp_path):
        with pytest.raises(GraphError) as caught:
            load_graph(tmp_path / "missing.json")

        assert caught.value.errors == (Problem("", "cannot read the file: No such file or directory"),)

    def test_reports_invalid_json_with_its_position(self):
        with pytest.raises(GraphError) as caught:
            parse_graph('{\n  // a comment\n  "start": "home"\n}')

        assert caught.value.errors == (
            Problem("", "not valid JSON: Expecting property name enclosed in double quotes at line 2, column 3"),
        )

    def test_rejects_nan(self):
        with pytest.raises(GraphError) as caught:
            parse_graph('{"p": NaN}')

        assert caught.value.errors == (Problem("", "not valid JSON: NaN is not allowed"),)

    def test_reports_a_duplicate_key(self):
        with pytest.raises(GraphError) as caught:
            parse_graph('{"nodes": {"home": {}, "home": {}}}')

        assert caught.value.errors == (Problem("", "duplicate key 'home'"),)

    def test_reports_every_problem_at_once(self, graph):
        graph["edges"]["home"][0]["p"] = 0.4
        graph["edges"]["search"][0]["to"] = "nowhere"

        assert errors_of(graph) == (
            Problem("edges.home", "probabilities sum to 0.9, not 1"),
            Problem("edges.search[0].to", "'nowhere' is not a node"),
        )


class TestSchema:
    def test_requires_every_top_level_field(self, graph):
        del graph["personas"]

        assert errors_of(graph) == (Problem("", "'personas' is a required property"),)

    def test_rejects_unknown_fields(self, graph):
        graph["edges"]["home"][0]["weight"] = 3

        assert errors_of(graph) == (
            Problem("edges.home[0]", "Additional properties are not allowed ('weight' was unexpected)"),
        )

    def test_reserves_exit_as_a_node_name(self, graph):
        graph["nodes"]["exit"] = {"request": "GET /bye"}

        assert errors_of(graph) == (
            Problem("nodes", "'exit' is reserved for leaving the graph and cannot be a node name"),
        )

    def test_requires_method_and_path_in_a_request(self, graph):
        graph["nodes"]["home"]["request"] = "FETCH /"

        assert errors_of(graph) == (Problem("nodes.home.request", "'FETCH /' is not METHOD /path"),)

    def test_requires_an_x_y_z_version(self, graph):
        graph["murmur_version"] = "latest"

        assert errors_of(graph) == (Problem("murmur_version", "'latest' is not an X.Y.Z version"),)

    def test_requires_a_jsonpath_for_extracts(self, graph):
        graph["nodes"]["search"]["extract"]["productId"]["path"] = "items[0].id"

        assert errors_of(graph) == (
            Problem("nodes.search.extract.productId.path", "'items[0].id' is not a JSONPath starting with $"),
        )

    def test_rejects_a_probability_above_one(self, graph):
        graph["edges"]["logout"][0]["p"] = 1.5

        assert errors_of(graph) == (Problem("edges.logout[0].p", "1.5 is greater than the maximum of 1"),)

    def test_rejects_a_negative_probability(self, graph):
        graph["edges"]["logout"][0]["p"] = -0.1

        assert errors_of(graph) == (Problem("edges.logout[0].p", "-0.1 is less than the minimum of 0"),)

    def test_requires_one_of_generate_or_value_in_a_test_rule(self, graph):
        graph["test_rules"]["test_otp"]["generate"] = "######"

        assert errors_of(graph) == (
            Problem("test_rules.test_otp", "a test rule needs exactly one of generate or value"),
        )

    def test_rejects_session_as_a_required_flag(self, graph):
        graph["nodes"]["logout"]["requires"] = ["@session"]

        assert errors_of(graph) == (
            Problem("nodes.logout.requires[0]", "'@session' may only contain letters, digits, _, . and -"),
        )


class TestHeaders:
    def test_allows_a_graph_without_top_level_headers(self, graph):
        del graph["headers"]

        assert parse_graph(json.dumps(graph)).headers == {}

    def test_rejects_an_extract_with_both_a_path_and_a_header(self, graph):
        graph["nodes"]["login"]["extract"]["token"]["path"] = "$.token"

        assert errors_of(graph) == (
            Problem("nodes.login.extract.token", "an extract reads either a path or a header, not both"),
        )

    def test_rejects_an_extract_with_neither_a_path_nor_a_header(self, graph):
        graph["nodes"]["login"]["extract"]["token"] = {"required": True}

        assert errors_of(graph) == (
            Problem(
                "nodes.login.extract.token",
                "an extract needs either path, pick and required, or header and required",
            ),
        )

    def test_requires_required_on_a_header_extract(self, graph):
        del graph["nodes"]["login"]["extract"]["token"]["required"]

        assert errors_of(graph) == (Problem("nodes.login.extract.token", "'required' is a required property"),)

    def test_requires_pick_on_a_body_extract(self, graph):
        del graph["nodes"]["search"]["extract"]["productId"]["pick"]

        assert errors_of(graph) == (Problem("nodes.search.extract.productId", "'pick' is a required property"),)

    def test_rejects_a_top_level_header_value_nothing_extracts(self, graph):
        graph["headers"]["Authorization"] = "Bearer {accessToken}"

        assert errors_of(graph) == (Problem("headers.Authorization", "{accessToken} is never extracted"),)

    def test_checks_generators_in_top_level_headers(self, graph):
        graph["headers"]["X-Request-Id"] = "{gen:request_id}"

        assert errors_of(graph) == (Problem("headers.X-Request-Id", "{gen:request_id} is not a known generator"),)

    def test_does_not_warn_about_a_header_value_extracted_later(self, graph):
        # {token} only exists after login; the header is left out until then.
        assert warnings_of(graph) == ()


class TestEdges:
    def test_requires_start_to_be_a_node(self, graph):
        graph["start"] = "landing"

        assert errors_of(graph) == (Problem("start", "'landing' is not a node"),)

    def test_requires_every_node_to_have_edges(self, graph):
        del graph["edges"]["logout"]

        assert errors_of(graph) == (Problem("edges.logout", "the node has no outgoing edges"),)

    def test_rejects_edges_for_an_unknown_node(self, graph):
        graph["edges"]["ghost"] = [{"to": "exit", "p": 1, "tag": "exit"}]

        assert errors_of(graph) == (Problem("edges.ghost", "'ghost' is not a node"),)

    def test_rejects_probabilities_summing_below_one(self, graph):
        graph["edges"]["signup"][0]["p"] = 0.65

        assert errors_of(graph) == (Problem("edges.signup", "probabilities sum to 0.95, not 1"),)

    def test_rejects_probabilities_summing_above_one(self, graph):
        graph["edges"]["signup"][0]["p"] = 0.8

        assert errors_of(graph) == (Problem("edges.signup", "probabilities sum to 1.1, not 1"),)

    def test_rejects_thirds_rounded_to_two_places(self, graph):
        graph["edges"]["logout"] = [
            {"to": "home", "p": 0.33, "tag": "browse"},
            {"to": "search", "p": 0.33, "tag": "browse"},
            {"to": "exit", "p": 0.33, "tag": "exit"},
        ]

        assert errors_of(graph) == (Problem("edges.logout", "probabilities sum to 0.99, not 1"),)

    def test_accepts_float_rounding_within_the_tolerance(self, graph):
        # 0.1 + 0.2 + 0.7 is 1.0000000000000002 in floating point.
        graph["edges"]["signup"] = [
            {"to": "search", "p": 0.1, "tag": "browse"},
            {"to": "home", "p": 0.2, "tag": "browse"},
            {"to": "exit", "p": 0.7, "tag": "exit"},
        ]
        graph["edges"]["logout"] = [
            {"to": "home", "p": 0.3333333, "tag": "browse"},
            {"to": "search", "p": 0.3333333, "tag": "browse"},
            {"to": "exit", "p": 0.3333334, "tag": "exit"},
        ]

        assert warnings_of(graph) == ()

    def test_rejects_a_sum_just_past_the_tolerance(self, graph):
        graph["edges"]["signup"][0]["p"] = 0.700002

        assert errors_of(graph) == (Problem("edges.signup", "probabilities sum to 1.000002, not 1"),)

    def test_requires_an_edge_to_exit(self, graph):
        graph["edges"]["signup"] = [{"to": "search", "p": 1, "tag": "browse"}]

        assert errors_of(graph) == (Problem("edges.signup", "no edge to exit"),)

    def test_rejects_an_edge_to_an_unknown_node(self, graph):
        graph["edges"]["signup"][0]["to"] = "profile"

        assert errors_of(graph) == (Problem("edges.signup[0].to", "'profile' is not a node"),)

    def test_requires_the_exit_tag_on_an_edge_to_exit(self, graph):
        graph["edges"]["signup"][1]["tag"] = "browse"

        assert errors_of(graph) == (Problem("edges.signup[1].tag", "an edge to exit must have the tag exit"),)

    def test_keeps_the_exit_tag_for_edges_to_exit(self, graph):
        graph["edges"]["signup"][0]["tag"] = "exit"

        assert errors_of(graph) == (Problem("edges.signup[0].tag", "the tag exit is only for edges to exit"),)

    def test_warns_about_a_node_unreachable_from_start(self, graph):
        graph["edges"]["home"][2]["p"] = 0
        graph["edges"]["home"][3]["p"] = 0.3

        assert warnings_of(graph) == (Problem("nodes.signup", "not reachable from start"),)


class TestPersonas:
    def test_requires_shares_to_sum_to_one(self, graph):
        graph["personas"]["buyer"]["share"] = 0.3

        assert errors_of(graph) == (Problem("personas", "shares sum to 0.9, not 1"),)

    def test_rejects_a_multiplier_for_a_tag_no_edge_has(self, graph):
        graph["personas"]["buyer"]["multipliers"]["transfer"] = 2

        assert errors_of(graph) == (
            Problem("personas.buyer.multipliers.transfer", "no edge has the tag 'transfer'"),
        )


class TestFlags:
    def test_rejects_a_required_flag_nothing_sets(self, graph):
        graph["nodes"]["checkout"]["requires"] = ["has_address"]

        assert errors_of(graph) == (Problem("nodes.checkout.requires", "flag 'has_address' is never set"),)

    def test_rejects_a_requires_not_flag_nothing_sets(self, graph):
        graph["nodes"]["login"]["requires_not"] = ["logged_in"]

        assert errors_of(graph) == (Problem("nodes.login.requires_not", "flag 'logged_in' is never set"),)

    def test_rejects_a_session_flag_nothing_sets(self, graph):
        graph["session_flags"].append("has_wishlist")

        assert errors_of(graph) == (Problem("session_flags", "flag 'has_wishlist' is never set"),)

    def test_rejects_clearing_a_flag_nothing_sets(self, graph):
        graph["nodes"]["checkout"]["skip"]["clears"] = ["has_basket"]

        assert errors_of(graph) == (Problem("nodes.checkout.skip.clears", "flag 'has_basket' is never set"),)

    def test_counts_flags_set_by_a_skip(self, graph):
        graph["nodes"]["checkout"]["skip"]["sets"] = ["has_order"]
        graph["nodes"]["logout"]["requires"] = ["authed", "has_order"]

        assert warnings_of(graph) == ()


class TestPlaceholders:
    def test_rejects_an_unknown_test_rule(self, graph):
        graph["nodes"]["signup"]["body"]["phone"] = "{test:test_phone}"

        assert errors_of(graph) == (Problem("nodes.signup.body", "{test:test_phone} names no test rule"),)

    def test_rejects_an_unknown_placeholder_kind(self, graph):
        graph["nodes"]["search"]["request"] = "GET /products?q={fake:query}"

        assert errors_of(graph) == (
            Problem("nodes.search.request", "unknown placeholder {fake:query}; use test, pool, gen or env"),
        )

    def test_rejects_a_value_nothing_extracts(self, graph):
        graph["nodes"]["checkout"]["request"] = "POST /checkout/{cartId}"

        assert errors_of(graph) == (Problem("nodes.checkout.request", "{cartId} is never extracted"),)

    def test_counts_values_extracted_by_a_skip(self, graph):
        graph["nodes"]["logout"]["body"] = {"lastOrder": "{orderId}"}

        assert warnings_of(graph) == ()

    def test_finds_placeholders_in_object_keys(self, graph):
        graph["nodes"]["checkout"]["body"] = {"{cartId}": 1}

        assert errors_of(graph) == (Problem("nodes.checkout.body", "{cartId} is never extracted"),)

    def test_rejects_an_unknown_generator(self, graph):
        graph["nodes"]["signup"]["body"]["name"] = "{gen:nickname}"

        assert errors_of(graph) == (Problem("nodes.signup.body", "{gen:nickname} is not a known generator"),)

    def test_accepts_every_known_generator(self, graph):
        graph["nodes"]["signup"]["body"]["fields"] = [f"{{gen:{name}}}" for name in GENERATORS]

        assert warnings_of(graph) == ()

    def test_checks_placeholders_inside_an_extract_path(self, graph):
        graph["nodes"]["checkout"]["extract"] = {
            "lineId": {"path": "$.lines[?(@.productId == '{productID}')].id", "pick": "first", "required": True}
        }

        assert errors_of(graph) == (
            Problem("nodes.checkout.extract.lineId.path", "{productID} is never extracted"),
        )

    def test_accepts_a_value_extracted_earlier_inside_an_extract_path(self, graph):
        graph["nodes"]["checkout"]["extract"] = {
            "lineId": {"path": "$.lines[?(@.productId == '{productId}')].id", "pick": "first", "required": True}
        }

        assert warnings_of(graph) == ()

    def test_checks_generators_inside_a_skip_extract_path(self, graph):
        graph["nodes"]["checkout"]["skip"]["extract"]["orderId"]["path"] = "$.orders[?(@.day == '{gen:tomorrow}')].id"

        assert errors_of(graph) == (
            Problem("nodes.checkout.skip.extract.orderId.path", "{gen:tomorrow} is not a known generator"),
        )

    def test_warns_when_a_value_can_be_used_before_it_is_extracted(self, graph):
        del graph["nodes"]["add_to_cart"]["requires"]
        graph["edges"]["home"][0]["p"] = 0.4
        graph["edges"]["home"].append({"to": "add_to_cart", "p": 0.1, "tag": "purchase"})

        assert warnings_of(graph) == (
            Problem(
                "nodes.add_to_cart.request",
                "{productId} can be used before any step extracts it; add a requires flag",
            ),
        )

    def test_does_not_warn_when_every_path_extracts_the_value_first(self, graph):
        del graph["nodes"]["add_to_cart"]["requires"]

        assert warnings_of(graph) == ()


class TestSkips:
    def test_requires_an_internal_murmur_endpoint(self, graph):
        graph["nodes"]["checkout"]["skip"]["request"] = "POST /orders/complete"

        assert errors_of(graph) == (
            Problem("nodes.checkout.skip.request", "a skip must call an /internal/murmur/ endpoint"),
        )

    def test_requires_the_murmur_key_header(self, graph):
        graph["nodes"]["checkout"]["skip"]["headers"] = {}

        assert errors_of(graph) == (
            Problem("nodes.checkout.skip.headers", "a skip must send X-Murmur-Key: {env:MURMUR_KEY}"),
        )

    def test_accepts_the_header_name_in_any_case(self, graph):
        graph["nodes"]["checkout"]["skip"]["headers"] = {"x-murmur-key": "{env:MURMUR_KEY}"}

        assert warnings_of(graph) == ()

    def test_rejects_a_hardcoded_key(self, graph):
        graph["nodes"]["checkout"]["skip"]["headers"] = {"X-Murmur-Key": "secret"}

        assert errors_of(graph) == (
            Problem("nodes.checkout.skip.headers", "a skip must send X-Murmur-Key: {env:MURMUR_KEY}"),
        )
