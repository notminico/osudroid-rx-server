"""End-to-end test of :class:`RankedMatchDriver` using a fake RoomChannel.

We intentionally bypass the database by monkey-patching
:mod:`objects.ranked.db` calls — the driver only persists results, it does
not read state from the DB during a series.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from objects.ranked import db as ranked_db
from objects.ranked.consts import GameMode, MatchPhase
from objects.ranked.match_driver import (
    Player,
    RankedMatchDriver,
    RankedSeries,
    _PoolEntry,
)


@dataclass
class FakeChannel:
    room_id: str = "test-room"
    events: List[Dict[str, Any]] = field(default_factory=list)
    last_md5: Optional[str] = None
    play_count: int = 0

    async def emit_event(self, event, data=None, *, to=None, skip_sid=None) -> None:
        self.events.append({"event": event, "data": data})

    async def set_beatmap(self, *, md5, title, artist, version) -> None:
        self.last_md5 = md5

    async def start_play(self) -> None:
        self.play_count += 1


@dataclass
class _DBStub:
    rounds: List[Dict[str, Any]] = field(default_factory=list)
    picks: List[Dict[str, Any]] = field(default_factory=list)
    finalized: Optional[Dict[str, Any]] = None
    applied: List[Dict[str, Any]] = field(default_factory=list)

    async def record_round(self, **kw):
        self.rounds.append(kw)

    async def record_pick_or_ban(self, **kw):
        self.picks.append(kw)

    async def finalize_match(self, **kw):
        self.finalized = kw

    async def apply_match_result(self, **kw):
        self.applied.append(kw)
        return {
            "user_id": kw["uid"],
            "mode": kw["mode"],
            "elo": kw["new_elo"],
            "peak_elo": kw["new_elo"],
            "wins": 1 if kw["won"] else 0,
            "losses": 0 if kw["won"] else 1,
            "games": 1,
            "tier": "Silver I",
            "placements_left": 4,
            "last_played": 0,
        }


@pytest.fixture
def db_stub(monkeypatch) -> _DBStub:
    stub = _DBStub()
    monkeypatch.setattr(ranked_db, "record_round", stub.record_round)
    monkeypatch.setattr(ranked_db, "record_pick_or_ban", stub.record_pick_or_ban)
    monkeypatch.setattr(ranked_db, "finalize_match", stub.finalize_match)
    monkeypatch.setattr(ranked_db, "apply_match_result", stub.apply_match_result)
    return stub


def _series(*, bo: int = 5) -> RankedSeries:
    pool = [
        _PoolEntry(slot="NM1", md5="aaa"),
        _PoolEntry(slot="NM2", md5="bbb"),
        _PoolEntry(slot="HD1", md5="ccc"),
        _PoolEntry(slot="HR1", md5="ddd"),
        _PoolEntry(slot="DT1", md5="eee"),
    ]
    tb = _PoolEntry(slot="TB", md5="zzz")
    return RankedSeries(
        match_id=1,
        mode=GameMode.SOLO_1V1,
        bo=bo,
        p1=Player(uid=10, elo_before=1000.0),
        p2=Player(uid=20, elo_before=1100.0),
        pool=pool,
        tiebreaker=tb,
    )


@pytest.mark.asyncio
async def test_full_flow_lower_elo_wins(db_stub):
    series = _series(bo=3)
    channel = FakeChannel()
    driver = RankedMatchDriver(series, channel)

    await driver.player_connected(10)
    await driver.player_connected(20)
    assert series.phase == MatchPhase.BAN
    # lowest ELO bans first
    assert series.next_banner_uid == 10

    assert await driver.submit_ban(10, "NM1") is True
    assert await driver.submit_ban(20, "NM2") is True
    assert series.phase == MatchPhase.PICK

    # picker should be the lower-elo player by default
    assert series.next_picker_uid == 10
    assert await driver.submit_pick(10, "HD1") is True
    assert series.phase == MatchPhase.ROUND

    await driver.round_finished(
        [{"uid": 10, "score": 900_000}, {"uid": 20, "score": 800_000}]
    )
    assert series.p1.score == 1
    assert series.phase == MatchPhase.PICK
    # losing side picks next
    assert series.next_picker_uid == 20

    assert await driver.submit_pick(20, "HR1") is True
    await driver.round_finished(
        [{"uid": 10, "score": 999_999}, {"uid": 20, "score": 100_000}]
    )
    assert series.finished
    assert series.phase == MatchPhase.FINISHED
    assert db_stub.finalized is not None
    assert db_stub.finalized["winner_uid"] == 10


@pytest.mark.asyncio
async def test_tiebreaker_kicks_in_when_tied(db_stub):
    series = _series(bo=2)
    channel = FakeChannel()
    driver = RankedMatchDriver(series, channel)
    await driver.player_connected(10)
    await driver.player_connected(20)
    await driver.submit_ban(10, "NM1")
    await driver.submit_ban(20, "NM2")

    await driver.submit_pick(10, "HD1")
    await driver.round_finished(
        [{"uid": 10, "score": 900_000}, {"uid": 20, "score": 800_000}]
    )
    await driver.submit_pick(20, "HR1")
    await driver.round_finished(
        [{"uid": 10, "score": 800_000}, {"uid": 20, "score": 900_000}]
    )

    # 1-1 in bo2 → tiebreaker
    assert series.phase == MatchPhase.TIEBREAKER
    assert series.current_pick is not None
    assert series.current_pick.slot == "TB"

    await driver.round_finished(
        [{"uid": 10, "score": 1_000_000}, {"uid": 20, "score": 0}]
    )
    assert series.finished
    assert db_stub.finalized["winner_uid"] == 10


@pytest.mark.asyncio
async def test_disconnect_forfeits(db_stub):
    series = _series(bo=5)
    driver = RankedMatchDriver(series, FakeChannel())
    await driver.player_connected(10)
    await driver.player_connected(20)
    await driver.player_disconnected(10)
    assert series.finished
    assert db_stub.finalized["winner_uid"] == 20
    assert db_stub.finalized["state"] == "forfeit"


@pytest.mark.asyncio
async def test_ban_validation(db_stub):
    series = _series()
    driver = RankedMatchDriver(series, FakeChannel())
    await driver.player_connected(10)
    await driver.player_connected(20)
    # not your turn
    assert await driver.submit_ban(20, "NM1") is False
    # invalid slot
    assert await driver.submit_ban(10, "BOGUS") is False
    # cannot ban the TB
    assert await driver.submit_ban(10, "TB") is False
    assert await driver.submit_ban(10, "NM1") is True
    # cannot double-ban
    assert await driver.submit_ban(20, "NM1") is False


@pytest.mark.asyncio
async def test_cannot_pick_banned_slot(db_stub):
    series = _series()
    driver = RankedMatchDriver(series, FakeChannel())
    await driver.player_connected(10)
    await driver.player_connected(20)
    await driver.submit_ban(10, "NM1")
    await driver.submit_ban(20, "NM2")
    assert await driver.submit_pick(10, "NM1") is False
    assert await driver.submit_pick(10, "HD1") is True
