import random

import requests

from murmur_runner.graph import Extract
from murmur_runner.http import judge, send
from murmur_runner.walker import Step


def step(**extracts):
    return Step("n", "GET", "/", {}, None, extracts, (), (), False)


RNG = random.Random(1)


class TestJudge:
    def test_succeeds_on_2xx_with_every_required_value(self):
        s = step(itemId=Extract("$.items[0].id", "first", True), token=Extract(None, "first", True, "Authorization"))

        assert judge(s, 201, {"Authorization": "Bearer t"}, '{"items": [{"id": 7}]}', RNG) == (
            {"itemId": 7, "token": "Bearer t"},
            None,
        )

    def test_fails_on_a_status_outside_2xx_with_an_excerpt_of_the_body(self):
        assert judge(step(), 409, {}, '{"error":\n  "ORDER_NOT_PAYABLE"}', RNG) == (
            None,
            'HTTP 409: {"error": "ORDER_NOT_PAYABLE"}',
        )

    def test_shortens_a_long_body(self):
        found, error = judge(step(), 500, {}, "x" * 500, RNG)

        assert error == "HTTP 500: " + "x" * 159 + "…"

    def test_fails_when_a_required_value_is_missing(self):
        s = step(itemId=Extract("$.items[0].id", "first", True))

        assert judge(s, 200, {}, '{"items": []}', RNG) == (None, "required extract itemId found nothing")

    def test_says_when_the_response_has_no_json_body(self):
        s = step(itemId=Extract("$.items[0].id", "first", True))

        assert judge(s, 200, {}, "<html>ok</html>", RNG) == (
            None,
            "required extract itemId found nothing (the response has no JSON body)",
        )

    def test_succeeds_when_only_an_optional_value_is_missing(self):
        s = step(itemId=Extract("$.items[0].id", "first", False))

        assert judge(s, 204, {}, "", RNG) == ({}, None)


class TestSend:
    def test_sends_the_method_path_headers_and_json_body(self, api):
        s = Step("login", "POST", "/login", {"X-Test": "1"}, {"email": "pool1@test.com", "password": "hunter22"},
                 {"token": Extract(None, "first", True, "Authorization")}, (), (), False)

        result = send(requests.Session(), api, s, RNG)

        assert (result.status, result.found, result.error) == (200, {"token": "Bearer tok-pool1@test.com"}, None)

    def test_reports_a_connection_failure(self):
        result = send(requests.Session(), "http://127.0.0.1:9", step(), RNG, timeout=2)

        assert (result.status, result.found, result.error) == (None, None, "request failed: could not connect")
