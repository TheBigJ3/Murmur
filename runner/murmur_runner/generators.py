"""Values for {gen:<name>} placeholders in a load graph.

Every generator takes a random.Random and the current time, so a run with a fixed seed
and clock produces the same values, except the ones that must be unique across runs and
processes (username and uuid), which ignore the seed: a repeated seed must not sign up
the same username twice. The mapping prompt lists these names and their formats; a graph
that uses any other name fails validation.
"""

from __future__ import annotations

import random
import secrets
import string
import uuid
from datetime import date, datetime, timezone
from typing import Callable

Generator = Callable[[random.Random, datetime], str]

FIRST_NAMES = (
    "Ava", "Ben", "Chloe", "Daniel", "Elena", "Felix", "Grace", "Hugo", "Isla", "Jonah",
    "Kara", "Leo", "Maya", "Noah", "Olivia", "Priya", "Quinn", "Rafael", "Sofia", "Theo",
)
LAST_NAMES = (
    "Adams", "Brooks", "Chen", "Diaz", "Evans", "Fischer", "Garcia", "Hughes", "Ito", "Jensen",
    "Khan", "Lopez", "Morris", "Nguyen", "Okafor", "Patel", "Rossi", "Silva", "Turner", "Walsh",
)
WORDS = (
    "music", "festival", "concert", "comedy", "theater", "sports", "food", "art", "jazz", "rock",
    "family", "outdoor", "weekend", "tickets", "tour", "live", "summer", "night", "club", "show",
    "dance", "film", "market", "workshop", "classic", "local", "vip", "game", "party", "series",
)
MIN_AGE = 18
MAX_AGE = 80


def _now_iso(rng: random.Random, now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _today(rng: random.Random, now: datetime) -> str:
    return now.astimezone(timezone.utc).date().isoformat()


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February in a year that has none
        return day.replace(year=day.year - years, day=28)


def _birth_date(rng: random.Random, now: datetime) -> str:
    """A date of birth for someone MIN_AGE to MAX_AGE years old today."""
    today = now.astimezone(timezone.utc).date()
    latest = _years_before(today, MIN_AGE)
    earliest = _years_before(today, MAX_AGE + 1).toordinal() + 1
    return date.fromordinal(rng.randint(earliest, latest.toordinal())).isoformat()


def _first_name(rng: random.Random, now: datetime) -> str:
    return rng.choice(FIRST_NAMES)


def _last_name(rng: random.Random, now: datetime) -> str:
    return rng.choice(LAST_NAMES)


def _full_name(rng: random.Random, now: datetime) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _username(rng: random.Random, now: datetime) -> str:
    return f"{rng.choice(FIRST_NAMES).lower()}{secrets.randbelow(10**8):08d}"


def _word(rng: random.Random, now: datetime) -> str:
    return rng.choice(WORDS)


def _sentence(rng: random.Random, now: datetime) -> str:
    words = [rng.choice(WORDS) for _ in range(rng.randint(5, 10))]
    return " ".join(words).capitalize() + "."


def _number(rng: random.Random, now: datetime) -> str:
    return str(rng.randint(1, 100))


PASSWORD_LENGTH = 16
PASSWORD_SYMBOLS = "!@#$%^&*"


def _password(rng: random.Random, now: datetime) -> str:
    """16 characters with at least one upper case letter, lower case letter, digit and
    symbol, which passes the usual password rules."""
    required = [
        rng.choice(string.ascii_uppercase), rng.choice(string.ascii_lowercase),
        rng.choice(string.digits), rng.choice(PASSWORD_SYMBOLS),
    ]
    alphabet = string.ascii_letters + string.digits + PASSWORD_SYMBOLS
    chars = required + [rng.choice(alphabet) for _ in range(PASSWORD_LENGTH - len(required))]
    rng.shuffle(chars)
    return "".join(chars)


def _uuid(rng: random.Random, now: datetime) -> str:
    return str(uuid.uuid4())


UNIQUE = ("username", "uuid")  # generators that ignore the seed

GENERATORS: dict[str, Generator] = {
    "now_iso": _now_iso,
    "today": _today,
    "birth_date": _birth_date,
    "first_name": _first_name,
    "last_name": _last_name,
    "full_name": _full_name,
    "username": _username,
    "query": _word,
    "word": _word,
    "sentence": _sentence,
    "number": _number,
    "password": _password,
    "uuid": _uuid,
}


def generate(name: str, rng: random.Random, now: datetime | None = None) -> str:
    """The value of {gen:<name>}. now defaults to the current time."""
    try:
        generator = GENERATORS[name]
    except KeyError:
        raise ValueError(f"unknown generator '{name}'") from None
    return generator(rng, now or datetime.now(timezone.utc))
