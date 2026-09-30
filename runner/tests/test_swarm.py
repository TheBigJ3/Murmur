import json
import re
import sys
from pathlib import Path

import pytest

from murmur_runner.cli import main
from murmur_runner.graph import parse_graph
from murmur_runner.swarm import LOCUSTFILE, Tally, locust_command, parse_shard, parse_think, warm_up

from conftest import POOL_ACCOUNTS
from test_trial import linear


class TestParse:
    def test_think_takes_a_range_or_one_number(self):
        assert parse_think("1-5") == (1.0, 5.0)
        assert parse_think("0.5") == (0.5, 0.5)

    @pytest.mark.parametrize("text", ["5-1", "-1", "a-b", ""])
    def test_think_rejects_a_bad_range(self, text):
        with pytest.raises(ValueError, match="think time"):
            parse_think(text)

    def test_shard_is_k_of_n(self):
        assert parse_shard("2/3") == (2, 3)

    @pytest.mark.parametrize("text", ["0/3", "4/3", "1", "a/b"])
    def test_shard_rejects_a_bad_value(self, text):
        with pytest.raises(ValueError, match="pool shard"):
            parse_shard(text)


class TestLocustCommand:
    def test_runs_locust_headless_with_the_murmur_settings(self, tmp_path):
        argv, env = locust_command(
            str(tmp_path / "g.json"), "http://localhost:3000", pool_path=str(tmp_path / "p.json"), pool_shard="1/2",
            users=20, spawn_rate=2.0, run_time="5m", think="1-3", seed=9, max_steps=100, web=False,
            extra=["--csv", "out"],
        )

        assert argv == [
            sys.executable, "-m", "locust", "-f", str(LOCUSTFILE), "--host", "http://localhost:3000",
            "--users", "20", "--spawn-rate", "2.0", "--run-time", "5m", "--headless", "--only-summary",
            "--csv", "out",
        ]
        assert {k: v for k, v in env.items() if k.startswith("MURMUR_") and k != "MURMUR_KEY"} == {
            "MURMUR_GRAPH": str(tmp_path / "g.json"),
            "MURMUR_POOL": str(tmp_path / "p.json"),
            "MURMUR_POOL_SHARD": "1/2",
            "MURMUR_THINK": "1-3",
            "MURMUR_SEED": "9",
            "MURMUR_MAX_STEPS": "100",
            "MURMUR_GROW": "1",
        }

    def test_starts_the_run_at_once_with_a_dashboard_on_this_machine_only(self, tmp_path):
        argv, env = locust_command(
            "g.json", "http://localhost:3000", pool_path=None, pool_shard=None, users=5, spawn_rate=1.0,
            run_time="2m", think="1-5", seed=1, max_steps=200, web=True, extra=[], web_port=9000,
        )

        assert argv[argv.index("--run-time"):] == [
            "--run-time", "2m", "--autostart", "--web-host", "127.0.0.1", "--web-port", "9000",
        ]
        assert "--headless" not in argv
        assert "MURMUR_POOL" not in env
        assert env["MURMUR_GRAPH"] == str(Path("g.json").resolve())

    def test_resets_the_statistics_after_ramp_up_when_asked(self):
        argv, _ = locust_command(
            "g.json", "http://localhost:3000", pool_path=None, pool_shard=None, users=5, spawn_rate=1.0,
            run_time="1m", think="1-5", seed=1, max_steps=200, web=False, extra=["--csv", "x"], reset_stats=True,
        )

        assert argv[-4:] == ["--only-summary", "--reset-stats", "--csv", "x"]


class TestTally:
    def test_prints_nothing_before_any_session(self):
        assert Tally().lines() == []

    def test_summarises_sessions_shortages_empty_steps_and_failures(self):
        tally = Tally()
        tally.personas.update({"buyer": 3, "browser": 5})
        tally.ended.update({"exit": 6, "step limit": 1})
        tally.no_account.update({"default": 2, "staff": 3})
        tally.grown = 4
        tally.joined = 1
        tally.found_nothing("my_orders", "orderId found nothing")
        tally.found_nothing("my_orders", "orderId found nothing")
        tally.failed("checkout", "HTTP 409: closed")
        tally.note("signup: no account to join owner")

        assert tally.lines(growable={"staff"}) == [
            "Murmur: 8 sessions (browser 5, buyer 3)",
            "Murmur: ended by exit 6, by the step limit 1, still running at the end 1",
            "Murmur: 2 sessions found no free account in default; add accounts to the pool",
            "Murmur: 3 sessions started without an account in staff, which fills as sessions create accounts",
            "Murmur: 4 new accounts joined the pool",
            "Murmur: accounts joined a group 1 times",
            "Murmur: steps that found nothing to extract, so their sets and clears were skipped (not failures)",
            "  my_orders: orderId found nothing (2x)",
            "Murmur: notes",
            "  signup: no account to join owner (1x)",
            "Murmur: failed steps",
            "  checkout: HTTP 409: closed (1x)",
        ]


class TestWarmUp:
    def test_sends_the_start_node_the_given_number_of_times(self, graph, api):
        results = warm_up(parse_graph(json.dumps(graph)), api, 3, seed=1)

        assert [r.status for r in results] == [200, 200, 200]

    def test_skips_a_start_node_that_needs_an_earlier_step(self, graph, api):
        graph["nodes"]["home"]["request"] = "GET /cart/{productId}"
        graph["nodes"]["home"]["requires"] = ["has_results"]
        graph["start"] = "home"

        assert warm_up(parse_graph(json.dumps(graph)), api, 3, seed=1) == "{productId} has not been extracted"


class TestSwarmCommand:
    def test_rejects_a_bad_think_time_before_any_check(self, graph, write_graph, capsys):
        assert main(["swarm", str(write_graph(graph)), "--host", "http://localhost", "--think", "5-1"]) == 1
        assert "think time '5-1' must be MIN-MAX seconds" in capsys.readouterr().err

    def test_rejects_a_shard_left_without_accounts_for_a_group(self, graph, api, write_graph, pool_file, murmur_key, capsys):
        # Two accounts dealt into five shards leave the fifth with none, and no step creates any.
        code = main(["swarm", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--pool-shard", "5/5"])

        assert code == 1
        assert capsys.readouterr().err.endswith(
            "murmur: the pool cannot serve this graph:\n  the pool group default has no accounts, and no step creates any\n"
        )

    def test_runs_a_real_swarm_and_prints_the_murmur_summary(self, graph, api, write_graph, pool_file, murmur_key, capfd):
        linear(graph, "home", "search", "login", "add_to_cart", "checkout", "logout")

        code = main([
            "swarm", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--users", "2",
            "--spawn-rate", "10", "--run-time", "3s", "--think", "0-0.05", "--seed", "5",
        ])

        out = capfd.readouterr()
        text = out.out + out.err
        assert code == 0, text
        assert f"murmur swarm: {api}, 2 users, seed 5" in text
        assert "Murmur: " in text and "sessions (browser" in text
        assert "checkout" in text and "Murmur: failed steps" not in text

    def test_found_nothing_does_not_fail_the_run(self, graph, api, write_graph, pool_file, murmur_key, capfd):
        graph["nodes"]["search"]["extract"]["productId"]["path"] = "$.missing[*].id"
        linear(graph, "home", "search", "login")

        code = main([
            "swarm", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--users", "2",
            "--spawn-rate", "10", "--run-time", "2s", "--think", "0-0.05", "--warm-up", "2",
        ])

        out = capfd.readouterr()
        text = out.out + out.err
        assert code == 0, text
        assert re.search(r"murmur: warm-up, 2 x home: 200 in \d+ ms, 200 in \d+ ms", text)
        assert "Murmur: steps that found nothing to extract" in text
        assert "  search: productId found nothing" in text


class TestCtrlC:
    # A stand-in for Locust: prints when it starts, and on Ctrl+C prints its summary and exits.
    CHILD = (
        "import signal, sys, time\n"
        "def stop(*_):\n"
        "    print('summary printed', flush=True); sys.exit(0)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "print('started', flush=True)\n"
        "time.sleep(30)\n"
    )
    PARENT = (
        "import sys\n"
        "from murmur_runner.cli import _run_locust\n"
        "sys.exit(_run_locust([sys.executable, '-c', sys.argv[1]], {}))\n"
    )

    def test_passes_ctrl_c_on_and_waits_for_the_summary(self):
        import os
        import signal
        import subprocess

        parent = subprocess.Popen(
            [sys.executable, "-c", self.PARENT, self.CHILD],
            stdout=subprocess.PIPE, text=True, env=dict(os.environ),
        )
        assert parent.stdout.readline() == "started\n"

        parent.send_signal(signal.SIGINT)  # only murmur gets it, not the child

        assert parent.stdout.read() == "summary printed\n"
        assert parent.wait(timeout=10) == 0


class TestPersonaCounts:
    def test_runs_a_fixed_count_persona_alongside_the_shares(self, graph, api, write_graph, tmp_path, murmur_key, capfd):
        pool = tmp_path / "pool.json"
        pool.write_text(json.dumps({
            "accounts": [{"email": "pool1@test.com", "password": "hunter22"}, {"email": "pool2@test.com", "password": "hunter33"}],
            "groups": {"staff": [{"email": "pool1@test.com", "password": "hunter22"}]},
        }))
        linear(graph, "home", "search")  # this also resets the personas
        graph["personas"]["staff"] = {"count": 1, "pool": "staff", "flags": ["is_staff"]}

        code = main([
            "swarm", str(write_graph(graph)), "--host", api, "--pool", str(pool), "--users", "3",
            "--spawn-rate", "10", "--run-time", "2s", "--think", "0-0.05",
        ])

        text = capfd.readouterr()
        text = text.out + text.err
        assert code == 0, text
        assert re.search(r"Murmur: \d+ sessions \(.*staff \d+", text), text


class TestProcesses:
    def test_refuses_locust_processes_while_a_pool_is_used(self, graph, api, write_graph, pool_file, murmur_key, capsys):
        code = main(["swarm", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--", "--processes", "2"])

        assert code == 1
        assert "Locust's --processes gives every process its own copy of the pool" in capsys.readouterr().err
