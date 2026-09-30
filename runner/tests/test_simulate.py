import json

from murmur_runner.graph import parse_graph
from murmur_runner.simulate import ENDED_EXIT, ENDED_LIMIT, format_report, simulate

# Two steps then exit, with no randomness: home, then list, then exit.
TINY = {
    "murmur_version": "0.3.0",
    "start": "home",
    "session_flags": [],
    "test_rules": {},
    "nodes": {"home": {"request": "GET /"}, "list": {"request": "GET /list"}, "admin": {"request": "GET /admin"}},
    "edges": {
        "home": [{"to": "list", "p": 1, "tag": "browse"}, {"to": "exit", "p": 0, "tag": "exit"}],
        "list": [{"to": "exit", "p": 1, "tag": "exit"}],
        "admin": [{"to": "exit", "p": 1, "tag": "exit"}],
    },
    "personas": {"visitor": {"share": 1}},
}


def load(data):
    return parse_graph(json.dumps(data))


class TestSimulate:
    def test_the_same_seed_gives_the_same_sessions(self, graph):
        loaded = load(graph)

        assert simulate(loaded, 300, seed=5).traces == simulate(loaded, 300, seed=5).traces

    def test_every_session_starts_at_start_and_ends_by_exit(self, graph):
        report = simulate(load(graph), 300, seed=5)

        assert all(t.steps[0].node == "home" for t in report.traces)
        assert report.ended() == {ENDED_EXIT: 300}

    def test_stops_a_session_at_the_step_limit(self, graph):
        graph["edges"]["logout"] = [{"to": "home", "p": 1, "tag": "browse"}, {"to": "exit", "p": 0, "tag": "exit"}]
        graph["edges"]["home"] = [{"to": "logout", "p": 1, "tag": "account"}, {"to": "exit", "p": 0, "tag": "exit"}]
        graph["nodes"]["logout"]["requires"] = []

        report = simulate(load(graph), 3, seed=1, max_steps=7)

        assert report.lengths() == [7, 7, 7]
        assert report.ended() == {ENDED_LIMIT: 3}

    def test_runs_every_session_as_the_given_persona(self, graph):
        report = simulate(load(graph), 50, seed=2, persona="buyer")

        assert {t.persona for t in report.traces} == {"buyer"}

    def test_records_a_step_that_uses_a_value_no_earlier_step_extracted(self, graph):
        del graph["nodes"]["add_to_cart"]["requires"]
        graph["edges"]["home"] = [{"to": "add_to_cart", "p": 1, "tag": "purchase"}, {"to": "exit", "p": 0, "tag": "exit"}]

        report = simulate(load(graph), 20, seed=3)

        assert report.failures()[("add_to_cart", "{productId} has not been extracted")] == 20


class TestFormatReport:
    def test_reports_the_traffic_mix(self):
        report = simulate(load(TINY), 4, seed=9)

        assert format_report(load(TINY), report, "g.json", show=2) == "\n".join([
            "g.json: 4 simulated sessions, seed 9",
            "",
            "Personas",
            "  visitor       4 sessions     2.0 steps on average",
            "",
            "Session length (steps)",
            "  mean 2.0, median 2, 95th percentile 2, longest 2",
            "  ended by exit: 4, hit the step limit: 0",
            "",
            "Requests",
            "  node     total  per session   share",
            "  home         4         1.00   50.0%",
            "  list         4         1.00   50.0%",
            "  never requested: admin",
            "",
            "Example sessions",
            "  visitor: home > list > exit",
            "  visitor: home > list > exit",
        ])

    def test_lists_failed_steps(self, graph):
        del graph["nodes"]["add_to_cart"]["requires"]
        graph["edges"]["home"] = [{"to": "add_to_cart", "p": 1, "tag": "purchase"}, {"to": "exit", "p": 0, "tag": "exit"}]
        report = simulate(load(graph), 2, seed=3)

        text = format_report(load(graph), report, "g.json", show=0)

        assert "Failed steps\n  add_to_cart: {productId} has not been extracted (2x)" in text
