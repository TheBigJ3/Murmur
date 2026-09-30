"""The test account pool, .murmur/pool.json: accounts that already exist on the target.

    {"accounts": [{"email": "pool1@test.com", "password": "...", "phone": "+10005550001"}]}

Each session leases one account as user, so no two sessions use the same account at
once, and borrows another as other_user, such as the recipient of a transfer, who may
be in a session of their own. A graph reads them as {pool:user.email} and
{pool:other_user.email}.
"""

from __future__ import annotations

import json
import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .graph import PLACEHOLDER, LoadGraph, json_strings

DEFAULT_POOL = ".murmur/pool.json"
ROLES = ("user", "other_user")


class PoolError(Exception):
    pass


@dataclass(frozen=True)
class Account:
    index: int
    fields: dict[str, str]


class Pool:
    def __init__(self, accounts: list[dict[str, str]]):
        self.accounts = [Account(i + 1, dict(fields)) for i, fields in enumerate(accounts)]
        self._leased: set[int] = set()
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: str | Path) -> Pool:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as e:
            raise PoolError(f"{path}: cannot read the file: {e.strerror}") from None
        except ValueError as e:
            raise PoolError(f"{path}: not valid JSON: {e}") from None
        accounts = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(accounts, list) or not accounts:
            raise PoolError(f'{path}: needs {{"accounts": [...]}} with at least one account')
        for i, account in enumerate(accounts, start=1):
            if not isinstance(account, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in account.items()
            ):
                raise PoolError(f"{path}: account {i} must be an object of text fields")
        return cls(accounts)

    def shard(self, index: int, count: int) -> Pool:
        """This machine's share of the accounts when several run a swarm: every count-th
        account, starting at index (1 based), so no two machines lease the same one."""
        if not 1 <= index <= count:
            raise PoolError(f"pool shard {index}/{count}: the index must be from 1 to {count}")
        mine = [a.fields for a in self.accounts[index - 1 :: count]]
        if not mine:
            raise PoolError(f"pool shard {index}/{count}: no accounts left for this shard")
        return Pool(mine)

    def lease(self, rng: random.Random) -> Account | None:
        """A free account, now leased, or None when every account is in use."""
        with self._lock:
            free = [a for a in self.accounts if a.index not in self._leased]
            if not free:
                return None
            account = rng.choice(free)
            self._leased.add(account.index)
            return account

    def release(self, account: Account) -> None:
        with self._lock:
            self._leased.discard(account.index)

    def other(self, rng: random.Random, exclude: Account | None) -> Account | None:
        """Any account other than exclude, leased or not."""
        others = [a for a in self.accounts if exclude is None or a.index != exclude.index]
        return rng.choice(others) if others else None

    def free(self) -> int:
        with self._lock:
            return len(self.accounts) - len(self._leased)


def pool_fields(graph: LoadGraph) -> dict[str, set[str]]:
    """The pool fields a graph uses, by role: {"user": {"email", "password"}, ...}."""
    used: dict[str, set[str]] = {}
    for text in _graph_strings(graph):
        for kind, value, _ in PLACEHOLDER.findall(text):
            if kind == "pool":
                role, _, field = value.partition(".")
                used.setdefault(role, set()).add(field)
    return used


def check_pool(graph: LoadGraph, pool: Pool | None) -> list[str]:
    """Problems that stop the pool from serving the graph, as messages."""
    used = pool_fields(graph)
    if not used:
        return []
    if pool is None:
        return [f"the graph uses pool accounts ({_describe(used)}) but there is no pool file"]
    problems = [
        f"the graph uses {{pool:{role}.…}}, but the pool only provides {' and '.join(ROLES)}"
        for role in sorted(used)
        if role not in ROLES
    ]
    fields = set().union(*used.values())
    for account in pool.accounts:
        missing = sorted(fields - set(account.fields))
        if missing:
            problems.append(f"account {account.index} has no {', '.join(missing)}")
    if "other_user" in used and len(pool.accounts) < 2:
        problems.append("the graph uses {pool:other_user.…}, which needs at least 2 accounts")
    return problems


def _describe(used: dict[str, set[str]]) -> str:
    return ", ".join(f"{role}.{field}" for role in sorted(used) for field in sorted(used[role]))


def _graph_strings(graph: LoadGraph) -> Iterator[str]:
    yield from graph.headers.values()
    for node in graph.nodes.values():
        for source in (node, node.skip):
            if source is None:
                continue
            yield source.request.path
            yield from json_strings(source.body)
            yield from (e.path for e in source.extract.values() if e.path)
        if node.skip:
            yield from json_strings(node.skip.headers)
