import io
import json
import random
import re

import pytest

from murmur_runner.cli import main
from murmur_runner.graph import parse_graph
from murmur_runner.pool import Pool
from murmur_runner.trial import run_try

from conftest import POOL_ACCOUNTS


def linear(graph, *path):
    """Rewire the graph so every session walks path, then exits."""
    for name in graph["nodes"]:
        graph["edges"][name] = [{"to": "exit", "p": 1, "tag": "exit"}]
    for here, there in zip(path, path[1:]):
        graph["edges"][here] = [{"to": there, "p": 1, "tag": "browse"}, {"to": "exit", "p": 0, "tag": "exit"}]
    graph["personas"] = {"browser": {"share": 1}}
    return graph


def run(graph, api, pool=None, **options):
    out = io.StringIO()
    failed = run_try(parse_graph(json.dumps(graph)), api, out, seed=3, pool=pool, **options)
    return failed, re.sub(r"\d+ ms", "N ms", out.getvalue())


@pytest.fixture(autouse=True)
def key(murmur_key):
    pass


class TestRunTry:
    def test_walks_a_full_session_with_tokens_skips_and_flags(self, graph, api):
        linear(graph, "home", "search", "login", "add_to_cart", "checkout", "logout")

        failed, out = run(graph, api, pool=Pool(POOL_ACCOUNTS["accounts"][:1]))

        lines = out.splitlines()
        assert failed == 0
        assert re.fullmatch(r"    2  search       GET /products\?q=[a-z]+  200  N ms  productId=p1", lines[2])
        assert lines[:2] + lines[3:] == [
            "session 1: browser, account 1",
            "    1  home         GET /  200  N ms",
            "    3  login        POST /login  200  N ms  token=Bearer tok-pool1@test.c…",
            "    4  add_to_cart  POST /cart/p1  200  N ms",
            "    5  checkout     POST /internal/murmur/complete-order (skip)  200  N ms  orderId=o1",
            "    6  logout       POST /logout  204  N ms",
            "  ended by exit after 6 steps, 0 failed",
            "1 sessions, 6 steps, 0 failed, 0 found nothing",
        ]

    def test_shows_the_reason_a_request_failed(self, graph, api):
        del graph["headers"]  # the token is never sent
        linear(graph, "home", "search", "login", "add_to_cart")

        failed, out = run(graph, api, pool=Pool(POOL_ACCOUNTS["accounts"]))

        assert failed == 1
        assert '    4  add_to_cart  POST /cart/p1  401  N ms  failed: HTTP 401: {"error": "unauthorized"}' in out

    def test_a_step_that_found_nothing_is_not_a_failure(self, graph, api):
        graph["nodes"]["search"]["extract"]["productId"]["path"] = "$.missing[*].id"
        linear(graph, "home", "search", "login", "add_to_cart")

        failed, out = run(graph, api, pool=Pool(POOL_ACCOUNTS["accounts"]))

        assert failed == 0
        assert re.search(r"    2  search +GET /products\?q=\w+  200  N ms  productId found nothing, so its sets and clears were skipped\n", out)
        # has_results was never set, so add_to_cart could not run: login is the last step.
        assert "  ended by exit after 3 steps, 0 failed" in out
        assert out.endswith("1 sessions, 3 steps, 0 failed, 1 found nothing\n")

    def test_shows_a_step_that_could_not_be_sent(self, graph, api):
        del graph["nodes"]["add_to_cart"]["requires"]
        linear(graph, "home", "add_to_cart")

        failed, out = run(graph, api, pool=Pool(POOL_ACCOUNTS["accounts"]))

        assert failed == 1
        assert "    2  add_to_cart  failed before sending: {productId} has not been extracted" in out

    def test_gives_every_session_a_fresh_cookie_jar(self, graph, api):
        graph["nodes"]["refresh_before"] = {"request": "GET /refresh"}
        graph["nodes"]["refresh_after"] = {"request": "GET /refresh"}
        linear(graph, "home", "refresh_before", "login", "refresh_after")

        failed, out = run(graph, api, pool=Pool(POOL_ACCOUNTS["accounts"]), sessions=2)

        # Each session's first refresh has no cookie yet; its second has the login's.
        assert re.findall(r"refresh_\w+ +GET /refresh +(\d+)", out) == ["401", "200", "401", "200"]
        assert failed == 2

    def test_runs_without_an_account_when_none_is_free(self, graph, api):
        pool = Pool(POOL_ACCOUNTS["accounts"][:1])
        pool.lease(random.Random(1))
        linear(graph, "home", "login")

        failed, out = run(graph, api, pool=pool)

        assert out.splitlines()[0] == "session 1: browser, no account yet"
        assert "    2  login        failed before sending: {pool:user.email}: this session has no user account" in out

    def test_releases_the_account_after_the_session(self, graph, api):
        pool = Pool(POOL_ACCOUNTS["accounts"][:1])
        linear(graph, "home", "login")

        _, out = run(graph, api, pool=pool, sessions=2)

        assert out.count(", account 1") == 2
        assert pool.free() == 1

    def test_stops_at_the_step_limit(self, graph, api):
        linear(graph, "home")
        graph["edges"]["home"] = [{"to": "home", "p": 1, "tag": "browse"}, {"to": "exit", "p": 0, "tag": "exit"}]

        _, out = run(graph, api, pool=Pool(POOL_ACCOUNTS["accounts"]), max_steps=3)

        assert "  ended by step limit after 3 steps, 0 failed" in out

    def test_waits_the_think_time_between_steps(self, graph, api):
        linear(graph, "home", "search")
        waits = []

        run(graph, api, pool=Pool(POOL_ACCOUNTS["accounts"]), think=1.5, sleep=waits.append)

        assert waits == [1.5, 1.5]


class TestTryCommand:
    def test_exits_zero_when_every_step_succeeds(self, graph, api, write_graph, pool_file, capsys):
        linear(graph, "home", "search")

        assert main(["try", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--seed", "1"]) == 0
        assert capsys.readouterr().out.startswith(f"murmur try: {api}, seed 1\n")

    def test_exits_one_when_a_step_fails(self, graph, api, write_graph, pool_file):
        del graph["headers"]
        linear(graph, "home", "search", "login", "add_to_cart")

        assert main(["try", str(write_graph(graph)), "--host", api, "--pool", str(pool_file)]) == 1

    def test_uses_the_project_pool_by_default(self, graph, api, write_graph, tmp_path, monkeypatch, capsys):
        (tmp_path / ".murmur").mkdir()
        (tmp_path / ".murmur" / "pool.json").write_text(json.dumps(POOL_ACCOUNTS))
        monkeypatch.chdir(tmp_path)
        linear(graph, "home", "login")

        assert main(["try", str(write_graph(graph)), "--host", api]) == 0

    def test_refuses_a_graph_whose_pool_is_missing(self, graph, api, write_graph, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        assert main(["try", str(write_graph(graph)), "--host", api]) == 1
        assert capsys.readouterr().err == (
            "murmur: the pool cannot serve this graph:\n"
            "  there is no pool file, and no step creates accounts for the pool group default\n"
        )

    def test_refuses_a_host_on_another_machine(self, graph, write_graph, pool_file, capsys):
        assert main(["try", str(write_graph(graph)), "--host", "https://dev.example.com", "--pool", str(pool_file)]) == 1
        assert capsys.readouterr().err == (
            "murmur: https://dev.example.com is not on this machine. "
            "Pass --yes if it is a dev server you mean to load.\n"
        )

    def test_refuses_a_target_that_fails_the_health_check(self, graph, api, write_graph, pool_file, monkeypatch, capsys):
        monkeypatch.setenv("MURMUR_KEY", "wrong")

        assert main(["try", str(write_graph(graph)), "--host", api, "--pool", str(pool_file)]) == 1
        assert "returned 404: the target is not in dev mode" in capsys.readouterr().err

    def test_can_skip_the_health_check_with_a_warning(self, graph, api, write_graph, pool_file, monkeypatch, capsys):
        monkeypatch.setenv("MURMUR_KEY", "wrong")
        linear(graph, "home")

        assert main(["try", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--no-preflight"]) == 0
        assert capsys.readouterr().err == "murmur: warning: skipping the health check; steps may reach real providers\n"

    def test_refuses_an_unknown_persona(self, graph, api, write_graph, pool_file, capsys):
        assert main(["try", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--persona", "admin"]) == 1
        assert capsys.readouterr().err == "murmur: no persona 'admin' (have: browser, buyer)\n"

    def test_takes_no_locust_options(self, graph, api, write_graph):
        with pytest.raises(SystemExit):
            main(["try", str(write_graph(graph)), "--host", api, "--", "--users", "5"])


def growing(graph):
    """Signup creates an account that can log in, and the shop's search grants a role."""
    graph["nodes"]["signup"]["body"] = {"email": "{test:test_email}", "password": "{gen:password}"}
    graph["nodes"]["signup"]["requires_not"] = ["@user"]
    graph["nodes"]["signup"]["account"] = {
        "fields": {"email": "{test:test_email}", "password": "{gen:password}"}, "ready": "authed",
    }
    graph["nodes"]["login"]["requires"] = ["@user"]
    return graph


class TestGrowth:
    def test_a_signup_grows_the_pool_and_a_later_session_logs_in_with_it(self, graph, api, tmp_path):
        growing(graph)
        graph["nodes"]["login"]["requires_not"] = ["authed"]
        linear(graph, "home", "signup", "login")
        grown = tmp_path / "pool.grown.json"
        pool = Pool.load(None, grown)

        failed, out = run(graph, api, pool=pool, sessions=2)

        assert failed == 0, out
        lines = out.splitlines()
        assert lines[0] == "session 1: browser, no account yet"
        assert "       account 1 (default) in pool.grown.json joined the pool" in lines
        assert lines[lines.index("  ended by exit after 2 steps, 0 failed") + 1] == "session 2: browser, account 1"
        saved = json.loads(grown.read_text())["groups"]["default"]
        assert len(saved) == 1 and saved[0]["email"].endswith("@test.com")

    def test_a_step_can_give_the_account_a_role(self, graph, api, tmp_path):
        growing(graph)
        graph["nodes"]["search"]["joins"] = ["owner"]
        linear(graph, "home", "signup", "search")
        pool = Pool.load(None, tmp_path / "pool.grown.json")

        run(graph, api, pool=pool)

        assert [a.groups for a in pool.accounts] == [{"default", "owner"}]

    def test_no_grow_keeps_the_pool_as_it_was(self, graph, api, tmp_path):
        growing(graph)
        linear(graph, "home", "signup")
        pool = Pool.load(None, None)

        failed, out = run(graph, api, pool=pool, grow=False)

        assert failed == 0 and pool.accounts == [] and "joined the pool" not in out

    def test_the_cli_saves_grown_accounts_next_to_the_pool(self, graph, api, write_graph, tmp_path, monkeypatch, murmur_key):
        growing(graph)
        linear(graph, "home", "signup")
        monkeypatch.chdir(tmp_path)

        assert main(["try", str(write_graph(graph)), "--host", api]) == 0
        assert (tmp_path / ".murmur" / "pool.grown.json").exists()

    def test_the_cli_notes_rate_limits_that_are_on(self, graph, api, write_graph, pool_file, capsys):
        linear(graph, "home")

        main(["try", str(write_graph(graph)), "--host", api, "--pool", str(pool_file)])

        assert "rate limits are on" in capsys.readouterr().err
