"""The test account pool: accounts that already exist on the target, in groups.

    .murmur/pool.json
    {"accounts": [{"email": "pool1@test.com", "password": "..."}]}           # the default group
    {"groups": {"customer": [{...}], "staff": [{"email": "...", ...}]}}      # named groups

An account is any set of text fields, in one or more groups: a group is whatever the
app needs, such as a role. An account listed under several groups is one account, so it
is never leased twice at once. Each session leases one account from its persona's group
as user, so no two sessions use the same account at once, and borrows
another from the same group as other_user, such as the recipient of a transfer, who may
be in a session of their own. A graph reads them as {pool:user.email} and
{pool:other_user.email}.

The pool grows: when a step creates an account and the session reaches the account's
ready flag, the account joins its group, and a step with joins adds the session's
account to more groups, such as a role it granted. Both are saved to
.murmur/pool.grown.json, next to the pool file, so later runs can use them too. Growing
can be turned off.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .graph import DEFAULT_GROUP, PLACEHOLDER, LoadGraph, json_strings

try:
    import fcntl
except ImportError:  # Windows: the grown file is written without a lock
    fcntl = None

DEFAULT_POOL = ".murmur/pool.json"
GROWN_NAME = "pool.grown.json"  # saved next to the pool file
ROLES = ("user", "other_user")


class PoolError(Exception):
    pass


POOL, GROWN, OTHER = "pool", "grown", "other"  # where an account was loaded from


@dataclass(eq=False)
class Account:
    index: int
    fields: dict[str, str]
    groups: set[str]
    source: str = POOL  # POOL: the pool file; GROWN: this run's grown file; OTHER: another grown file
    origin: str = ""  # the file it was loaded from, if any

    @property
    def grown(self) -> bool:
        return self.source != POOL

    def describe(self) -> str:
        where = f" in {Path(self.origin).name}" if self.origin else ""
        return f"account {self.index} ({', '.join(sorted(self.groups))}){where}"


class Pool:
    def __init__(
        self,
        accounts: list[dict[str, str]] | None = None,
        *,
        groups: dict[str, list[dict[str, str]]] | None = None,
        grown_path: str | Path | None = None,
    ):
        self.accounts: list[Account] = []
        self.grown_path = Path(grown_path) if grown_path else None
        self._leased: set[int] = set()
        self._lock = threading.Lock()
        for fields in accounts or []:
            self._append(fields, {DEFAULT_GROUP}, POOL)
        for group, members in (groups or {}).items():
            for fields in members:
                self._append(fields, {group}, POOL)

    @classmethod
    def load(
        cls,
        path: str | Path | None,
        grown_path: str | Path | None = None,
        others: list[str | Path] = (),
    ) -> Pool:
        """The accounts in path, if given, plus the grown accounts saved at grown_path and
        in the other grown files (such as other shards'), where those files exist. New
        grown accounts are saved to grown_path only."""
        pool = cls(grown_path=grown_path)
        sources = [(path, POOL, True)] if path is not None else []
        if grown_path is not None:
            sources.append((grown_path, GROWN, False))
        sources += [(other, OTHER, False) for other in others]
        for file, source, required in sources:
            if not required and not Path(file).exists():
                continue
            for group, members in _read(file, required=required).items():
                for fields in members:
                    pool._append(fields, {group}, source, str(file))
        return pool

    def _append(self, fields: dict[str, str], groups: set[str], source: str, origin: str = "") -> Account:
        """Add an account, or add groups to the account with exactly these fields."""
        existing = self.find(fields)
        if existing is not None:
            existing.groups |= groups
            return existing
        account = Account(len(self.accounts) + 1, dict(fields), set(groups), source, origin)
        self.accounts.append(account)
        return account

    def find(self, fields: dict[str, Any]) -> Account | None:
        text = {k: str(v) for k, v in fields.items()}
        return next((a for a in self.accounts if a.fields == text), None)

    def drop(self, accounts: list[Account]) -> None:
        dropped = {a.index for a in accounts}
        self.accounts = [a for a in self.accounts if a.index not in dropped]

    def group(self, name: str) -> list[Account]:
        return [a for a in self.accounts if name in a.groups]

    def shard(self, index: int, count: int) -> Pool:
        """This machine's share when several run a swarm, so no two machines lease the
        same account. The accounts of this shard's own grown file all stay: it saves them
        (see grown_name), so they are already this shard's. The rest, from the pool file
        and other grown files, are sorted by a hash of their fields and dealt out in
        turn, which splits them evenly and the same way on every machine. A shard can end
        up with no accounts for a group; check_pool decides whether that matters."""
        if not 1 <= index <= count:
            raise PoolError(f"pool shard {index}/{count}: the index must be from 1 to {count}")
        mine = Pool(grown_path=self.grown_path)
        shared = sorted((a for a in self.accounts if a.source != GROWN), key=lambda a: _hash(a.fields))
        dealt = {a.index for a in shared[index - 1 :: count]}
        for account in self.accounts:
            if account.source == GROWN or account.index in dealt:
                mine._append(account.fields, account.groups, account.source, account.origin)
        return mine

    def lease(self, rng: random.Random, group: str = DEFAULT_GROUP) -> Account | None:
        """A free account of group, now leased, or None when every one is in use."""
        with self._lock:
            free = [a for a in self.accounts if group in a.groups and a.index not in self._leased]
            if not free:
                return None
            account = rng.choice(free)
            self._leased.add(account.index)
            return account

    def release(self, account: Account) -> None:
        with self._lock:
            self._leased.discard(account.index)

    def other(self, rng: random.Random, exclude: Account | None, group: str = DEFAULT_GROUP) -> Account | None:
        """Any account of group other than exclude, leased or not."""
        others = [a for a in self.accounts if group in a.groups and (exclude is None or a.index != exclude.index)]
        return rng.choice(others) if others else None

    def free(self, group: str | None = None) -> int:
        with self._lock:
            return sum(1 for a in self.accounts if (group is None or group in a.groups) and a.index not in self._leased)

    def add(self, groups: str | tuple[str, ...] | list[str], fields: dict[str, Any], leased: bool = False) -> Account:
        """Add an account a session created to one group or several, leased to that
        session when leased is true, and save it with the grown accounts."""
        groups = (groups,) if isinstance(groups, str) else tuple(groups)
        with self._lock:
            account = self._append({k: str(v) for k, v in fields.items()}, set(groups), GROWN, str(self.grown_path or ""))
            if leased:
                self._leased.add(account.index)
        self._save(account, groups)
        return account

    def join(self, account: Account, groups: str | list[str] | tuple[str, ...]) -> list[str]:
        """Add account to more groups, such as a role a step granted it, and save that
        with the grown accounts. Returns the groups it was not in before."""
        groups = (groups,) if isinstance(groups, str) else groups
        with self._lock:
            new = [g for g in dict.fromkeys(groups) if g not in account.groups]
            account.groups |= set(new)
        self._save(account, new)
        return new

    def _save(self, account: Account, groups) -> None:
        if self.grown_path is not None:
            for group in groups:
                _save_grown(self.grown_path, group, account.fields)


def _read(path: str | Path, required: bool) -> dict[str, list[dict[str, str]]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise PoolError(f"{path}: cannot read the file: {e.strerror}") from None
    except ValueError as e:
        raise PoolError(f"{path}: not valid JSON: {e}") from None
    if not isinstance(data, dict) or not ({"accounts", "groups"} & set(data)):
        raise PoolError(f'{path}: needs {{"accounts": [...]}} or {{"groups": {{"<name>": [...]}}}}')
    unknown = sorted(set(data) - {"accounts", "groups"})
    if unknown:
        raise PoolError(f"{path}: unknown key {', '.join(unknown)}; use accounts or groups")
    # Each list with the place it sits in the file, for messages.
    lists: list[tuple[str, str, Any]] = []
    if "accounts" in data:
        lists.append((DEFAULT_GROUP, "accounts", data["accounts"]))
    if "groups" in data:
        if not isinstance(data["groups"], dict):
            raise PoolError(f'{path}: "groups" must map group names to lists of accounts')
        lists += [(name, f"groups.{name}", members) for name, members in data["groups"].items()]
    groups: dict[str, list[dict[str, str]]] = {}
    for name, where, members in lists:
        if not isinstance(members, list):
            raise PoolError(f"{path}: {where} must be a list of accounts")
        for i, account in enumerate(members):
            if not isinstance(account, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in account.items()
            ):
                raise PoolError(f"{path}: {where}[{i}] must be an object of text fields")
        groups[name] = groups.get(name, []) + members  # "accounts" and groups.default together
    if required and not any(groups.values()):
        raise PoolError(f"{path}: has no accounts")
    return groups


def grown_name(shard: tuple[int, int] | None = None) -> str:
    """The grown accounts file's name: one per shard, so machines and processes that
    share a directory never lease or overwrite each other's grown accounts."""
    return GROWN_NAME if shard is None else f"pool.grown.{shard[0]}of{shard[1]}.json"


def _hash(fields: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def grown_files(folder: str | Path) -> list[Path]:
    """Every grown accounts file in folder: the unsharded one and each shard's."""
    return sorted(Path(folder).glob("pool.grown*.json"))


def _save_grown(path: Path, group: str, fields: dict[str, str]) -> None:
    """Add one account to the grown file, keeping what other processes saved there. A
    lock file keeps two processes from writing at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f".{path.name}.lock"), "w") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            groups = _read(path, required=False) if path.exists() else {}
        except PoolError:
            groups = {}
        members = groups.setdefault(group, [])
        if fields not in members:
            members.append(fields)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"groups": groups}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


def pool_fields(graph: LoadGraph) -> dict[str, set[str]]:
    """The pool fields a graph uses, by role: {"user": {"email", "password"}, ...}."""
    used: dict[str, set[str]] = {}
    for text in _graph_strings(graph):
        for kind, value, _ in PLACEHOLDER.findall(text):
            if kind == "pool":
                role, _, field = value.partition(".")
                used.setdefault(role, set()).add(field)
    return used


def growable_groups(graph: LoadGraph) -> dict[str, list[str]]:
    """The groups steps add accounts to, by creating them or joining them to a group,
    with the nodes that do it."""
    creators: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        for source in (node, node.skip):
            if source is None:
                continue
            groups = ([source.account.group] if source.account else []) + list(source.joins)
            for group in groups:
                if node.name not in creators.setdefault(group, []):
                    creators[group].append(node.name)
    return creators


def check_pool(graph: LoadGraph, pool: Pool | None) -> list[str]:
    """Problems that stop the pool from serving the graph, as messages. A group with no
    accounts is fine when a step can add some, even with growing off: sessions then use
    the accounts they create themselves (see pool_notes)."""
    used = pool_fields(graph)
    if not used:
        return []
    creators = growable_groups(graph)
    problems = []  # the loader already rejects roles other than user and other_user
    fields = set().union(*used.values())
    for node in graph.nodes.values():
        for source in (node, node.skip):
            if source is not None and source.account is not None:
                # A created account is later leased as user and borrowed as other_user.
                missing = sorted(fields - set(source.account.fields))
                if missing:
                    problems.append(
                        f"nodes.{node.name} creates accounts with no {', '.join(missing)}, "
                        "which the graph uses as {pool:…}"
                    )
    for account in pool.accounts if pool else []:
        lacking = sorted(fields - set(account.fields))
        if lacking and not account.grown:
            problems.append(f"{account.describe()} has no {', '.join(lacking)}")
    for name in dict.fromkeys(p.pool for p in graph.personas.values()):
        members = pool.group(name) if pool else []
        if not members and name not in creators:
            if pool is None:
                problems.append(f"there is no pool file, and no step creates accounts for the pool group {name}")
            else:
                problems.append(f"the pool group {name} has no accounts, and no step creates any")
            continue
        if "other_user" in used and len(members) < 2 and name not in creators:
            problems.append(f"the graph uses {{pool:other_user.…}}, which needs at least 2 accounts in {name}")
    return problems


def drop_incomplete_grown(graph: LoadGraph, pool: Pool | None) -> list[str]:
    """Leave out grown accounts that lack a field the graph now uses, such as ones grown
    before the graph asked for a phone number. Returns a note about each file they are in."""
    if pool is None:
        return []
    fields = set().union(*pool_fields(graph).values()) if pool_fields(graph) else set()
    incomplete = [a for a in pool.accounts if a.grown and fields - set(a.fields)]
    pool.drop(incomplete)
    by_file: dict[str, list[Account]] = {}
    for account in incomplete:
        by_file.setdefault(Path(account.origin).name or "memory", []).append(account)
    return [
        f"left out {len(accounts)} grown accounts in {name} that have no "
        f"{', '.join(sorted(set().union(*(fields - set(a.fields) for a in accounts))))}; "
        f"delete {name} to start growing afresh"
        for name, accounts in by_file.items()
    ]


def pool_notes(graph: LoadGraph, pool: Pool | None, grow: bool) -> list[str]:
    """Things worth knowing that don't stop a run: groups that stay empty because
    growing is off, and fixed-count personas with fewer accounts than users."""
    if not pool_fields(graph):
        return []
    creators = growable_groups(graph)
    notes = []
    for persona in graph.personas.values():
        members = len(pool.group(persona.pool)) if pool else 0
        if not grow and not members and persona.pool in creators:
            notes.append(
                f"the pool group {persona.pool} has no accounts and --no-grow keeps new ones out, so "
                f"{persona.name} sessions only have the accounts they create themselves"
            )
        elif persona.count and members < persona.count and not (grow and persona.pool in creators):
            notes.append(
                f"{persona.name} runs {persona.count} users but the pool group {persona.pool} has "
                f"{members} accounts, so some of them run without one"
            )
    return notes


def _graph_strings(graph: LoadGraph) -> Iterator[str]:
    yield from graph.headers.values()
    for node in graph.nodes.values():
        for source in (node, node.skip):
            if source is None:
                continue
            yield source.request.path
            yield from json_strings(source.body)
            yield from (e.path for e in source.extract.values() if e.path)
            yield from json_strings(source.post)
        if node.skip:
            yield from json_strings(node.skip.headers)
