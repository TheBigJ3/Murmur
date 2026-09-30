import sys
from pathlib import Path

import pytest

from murmur_runner.cli import main
from murmur_runner.swarm import LOCUSTFILE, locust_command, parse_shard, parse_think

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
        }

    def test_opens_the_web_interface_without_a_run_time(self, tmp_path):
        argv, env = locust_command(
            "g.json", "http://localhost:3000", pool_path=None, pool_shard=None, users=5, spawn_rate=1.0,
            run_time=None, think="1-5", seed=1, max_steps=200, web=True, extra=[],
        )

        assert "--headless" not in argv and "--run-time" not in argv
        assert "MURMUR_POOL" not in env
        assert env["MURMUR_GRAPH"] == str(Path("g.json").resolve())


class TestSwarmCommand:
    def test_rejects_a_bad_think_time_before_any_check(self, graph, write_graph, capsys):
        assert main(["swarm", str(write_graph(graph)), "--host", "http://localhost", "--think", "5-1"]) == 1
        assert "think time '5-1' must be MIN-MAX seconds" in capsys.readouterr().err

    def test_rejects_a_shard_with_no_accounts(self, graph, api, write_graph, pool_file, murmur_key, capsys):
        assert main(["swarm", str(write_graph(graph)), "--host", api, "--pool", str(pool_file), "--pool-shard", "3/3"]) == 1
        assert capsys.readouterr().err == "murmur: pool shard 3/3: no accounts left for this shard\n"

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
