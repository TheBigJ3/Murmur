import json

from murmur_runner.cli import main


class TestValidate:
    def test_reports_a_valid_graph_and_exits_zero(self, graph, write_graph, capsys):
        path = write_graph(graph)

        assert main(["validate", str(path)]) == 0
        assert capsys.readouterr().out == f"{path}: valid, 7 nodes, 20 edges, 2 personas, 0 warnings\n"

    def test_prints_warnings_for_a_valid_graph(self, graph, write_graph, capsys):
        graph["edges"]["home"][2]["p"] = 0
        graph["edges"]["home"][3]["p"] = 0.3
        path = write_graph(graph)

        assert main(["validate", str(path)]) == 0
        assert capsys.readouterr().out == (
            f"{path}: valid, 7 nodes, 20 edges, 2 personas, 1 warning\n"
            "  warning  nodes.signup: not reachable from start\n"
        )

    def test_prints_every_error_and_exits_one(self, graph, write_graph, capsys):
        graph["edges"]["home"][0]["p"] = 0.4
        graph["personas"]["buyer"]["share"] = 0.3
        path = write_graph(graph)

        assert main(["validate", str(path)]) == 1
        assert capsys.readouterr().err == (
            f"{path}: 2 errors, 0 warnings\n"
            "  error    edges.home: probabilities sum to 0.9, not 1\n"
            "  error    personas: shares sum to 0.9, not 1\n"
        )

    def test_reads_the_project_graph_by_default(self, graph, tmp_path, monkeypatch, capsys):
        (tmp_path / ".murmur").mkdir()
        (tmp_path / ".murmur" / "loadgraph.json").write_text(json.dumps(graph))
        monkeypatch.chdir(tmp_path)

        assert main(["validate"]) == 0
        assert capsys.readouterr().out.startswith(".murmur/loadgraph.json: valid")
