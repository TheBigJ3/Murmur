import random
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from murmur_runner.generators import FIRST_NAMES, GENERATORS, LAST_NAMES, UNIQUE, WORDS, generate

NOW = datetime(2026, 9, 29, 18, 5, 7, 123456, tzinfo=timezone.utc)


def values(name: str, count: int = 200, now: datetime = NOW) -> list[str]:
    rng = random.Random(42)
    return [generate(name, rng, now) for _ in range(count)]


class TestGenerate:
    def test_now_iso_is_utc_with_milliseconds(self):
        assert values("now_iso", 1) == ["2026-09-29T18:05:07.123Z"]

    def test_now_iso_converts_other_time_zones_to_utc(self):
        local = NOW.astimezone(timezone(timedelta(hours=-7)))

        assert values("now_iso", 1, now=local) == ["2026-09-29T18:05:07.123Z"]

    def test_today_is_the_utc_date(self):
        assert values("today", 1) == ["2026-09-29"]

    def test_birth_date_is_at_least_18_and_under_81_years_ago(self):
        dates = [date.fromisoformat(v) for v in values("birth_date", 2000)]

        assert max(dates) <= date(2008, 9, 29)
        assert min(dates) > date(1945, 9, 29)

    def test_birth_date_on_29_february_falls_back_to_the_28th(self):
        leap_day = datetime(2028, 2, 29, tzinfo=timezone.utc)

        dates = [date.fromisoformat(v) for v in values("birth_date", 2000, now=leap_day)]

        assert max(dates) <= date(2010, 2, 28)
        assert min(dates) > date(1947, 2, 28)

    def test_names_come_from_the_name_lists(self):
        assert set(values("first_name")) <= set(FIRST_NAMES)
        assert set(values("last_name")) <= set(LAST_NAMES)
        for full in values("full_name"):
            first, last = full.split(" ")
            assert first in FIRST_NAMES and last in LAST_NAMES

    def test_username_is_a_lowercase_name_and_digits(self):
        for username in values("username"):
            assert re.fullmatch(r"[a-z]+[0-9]{8}", username)

    def test_query_and_word_are_single_words(self):
        assert set(values("query")) <= set(WORDS)
        assert set(values("word")) <= set(WORDS)

    def test_sentence_has_five_to_ten_words_and_a_full_stop(self):
        for sentence in values("sentence"):
            assert sentence.endswith(".") and sentence[0].isupper()
            assert 5 <= len(sentence[:-1].split(" ")) <= 10

    def test_number_is_between_1_and_100(self):
        numbers = {int(v) for v in values("number", 2000)}

        assert min(numbers) == 1 and max(numbers) == 100

    def test_password_mixes_upper_lower_digit_and_symbol(self):
        for password in values("password"):
            assert len(password) == 16
            assert re.search("[A-Z]", password) and re.search("[a-z]", password)
            assert re.search("[0-9]", password) and re.search("[!@#$%^&*]", password)

    def test_uuid_is_version_4(self):
        for value in values("uuid"):
            assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value)

    def test_the_same_seed_gives_the_same_values(self):
        for name in set(GENERATORS) - set(UNIQUE):
            assert values(name, 20) == values(name, 20)

    def test_unique_values_differ_even_with_the_same_seed(self):
        for name in UNIQUE:
            assert values(name, 20) != values(name, 20)

    def test_rejects_an_unknown_generator(self):
        with pytest.raises(ValueError, match="^unknown generator 'nickname'$"):
            generate("nickname", random.Random(1), NOW)
