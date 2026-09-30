import pytest

from murmur_runner.safety import SafetyError, check_host, is_local, preflight


class TestCheckHost:
    def test_drops_a_trailing_slash(self):
        assert check_host("http://localhost:3000/") == "http://localhost:3000"

    @pytest.mark.parametrize("host", ["localhost:3000", "ftp://x.com", "http://"])
    def test_rejects_anything_but_an_http_url(self, host):
        with pytest.raises(SafetyError, match="must be an http:// or https:// URL"):
            check_host(host)


class TestIsLocal:
    @pytest.mark.parametrize(
        "host", ["http://localhost:3000", "http://api.localhost", "http://127.0.0.1", "http://[::1]:8000", "http://0.0.0.0"]
    )
    def test_accepts_this_machine(self, host):
        assert is_local(host)

    @pytest.mark.parametrize("host", ["https://dev.example.com", "http://10.0.0.5", "http://localhost.example.com"])
    def test_rejects_other_machines(self, host):
        assert not is_local(host)


class TestPreflight:
    def test_passes_when_the_health_check_answers_200(self, api):
        preflight(api, {"MURMUR_KEY": "key-123"})

    def test_needs_the_key(self, api):
        with pytest.raises(SafetyError, match=r"^MURMUR_KEY is not set"):
            preflight(api, {})

    def test_refuses_a_404_from_a_wrong_key_or_a_prod_build(self, api):
        with pytest.raises(SafetyError, match=r"returned 404: the target is not in dev mode.*Refusing to run"):
            preflight(api, {"MURMUR_KEY": "wrong"})

    def test_refuses_an_unreachable_host(self):
        with pytest.raises(SafetyError, match=r"failed: could not reach http://127.0.0.1:9$"):
            preflight("http://127.0.0.1:9", {"MURMUR_KEY": "k"}, timeout=2)
