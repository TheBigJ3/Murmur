"""One simulated user, running sessions one after another.

murmur try and murmur swarm both drive sessions through an Actor, so they lease, grow
and share in the same way. For each session it picks the persona (the actor's fixed one,
or one drawn by share), leases a user account from the persona's pool group and borrows
another as other_user, and starts the session with the shared board. After each step it
adds accounts the session made usable to the pool, where they become the session's
leased user. At the end of a session it releases what it leased.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import Mapping

from .board import Board
from .graph import LoadGraph, Persona
from .pool import Account, Pool
from .walker import Session, pick_persona


@dataclass
class Settled:
    """What one step changed in the pool: accounts it added, and groups accounts joined."""

    new: list[Account] = field(default_factory=list)
    joined: list[tuple[Account, list[str]]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.new or self.joined)


class Actor:
    def __init__(
        self,
        graph: LoadGraph,
        rng: random.Random,
        *,
        pool: Pool | None,
        board: Board,
        env: Mapping[str, str] | None = None,
        persona: str | None = None,
        grow: bool = True,
    ):
        self.graph = graph
        self.rng = rng
        self.pool = pool
        self.board = board
        self.env = env if env is not None else os.environ
        self.fixed = graph.personas[persona] if persona else None
        self.grow = grow
        self.session: Session | None = None
        self.account: Account | None = None
        self.grown: list[Account] = []

    def begin(self) -> Session:
        persona: Persona = self.fixed or pick_persona(self.graph, self.rng)
        accounts = {}
        if self.pool is not None:
            self.account = self.pool.lease(self.rng, persona.pool)
            if self.account is not None:
                accounts["user"] = self.account.fields
            other = self.pool.other(self.rng, self.account, persona.pool)
            if other is not None:
                accounts["other_user"] = other.fields
        self.session = Session(
            self.graph, persona, self.rng, pool=accounts, env=self.env, board=self.board,
            groups=set(self.account.groups) if self.account else set(),
        )
        return self.session

    def settle(self) -> Settled:
        """After a step: add accounts the session made usable to the pool, leased to this
        session, and add the session's account to groups a step joined it to."""
        settled = Settled()
        for groups, fields in self.session.take_ready_accounts():
            if self.pool is None or not self.grow:
                continue
            existing = self.pool.find(fields)
            before = set(existing.groups) if existing else set()
            account = self.pool.add(groups, fields, leased=True)
            if self.account is not None and self.account is not account:
                self.pool.release(self.account)
            self.account = account
            if existing is None:
                settled.new.append(account)
            elif set(groups) - before:
                settled.joined.append((account, sorted(set(groups) - before)))
        joins = self.session.take_joins()
        if joins and self.account is not None and self.pool is not None and self.grow:
            new_groups = self.pool.join(self.account, joins)
            if new_groups:
                settled.joined.append((self.account, new_groups))
        self.grown += settled.new
        return settled

    def end(self) -> None:
        if self.account is not None and self.pool is not None:
            self.pool.release(self.account)
        self.account = None
        self.session = None
