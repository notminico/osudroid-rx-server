"""Drives a ranked Bo-N series inside an ordinary multiplayer room.

The state machine:

    WAITING  -> BAN     when both players connect
    BAN      -> PICK    after ``DEFAULT_BANS_PER_PLAYER * 2`` bans
    PICK     -> ROUND   when a beatmap is picked
    ROUND    -> PICK    while neither player has reached ``win_threshold``
    ROUND    -> TIEBREAKER  if Bo-N is exhausted with a tie
    TIEBREAKER -> ROUND  one final round on the TB slot
    ROUND    -> FINISHED when a side reaches ``win_threshold``
    *        -> ABORTED on critical failure / forfeit

The driver does NOT speak Socket.IO directly; instead it talks to a small
:class:`RoomChannel` adapter so we can unit-test it with a fake.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

from . import db as ranked_db
from .consts import (
    DEFAULT_BANS_PER_PLAYER,
    DEFAULT_BO,
    GameMode,
    MatchPhase,
)
from .elo import update_elo
from .tiers import tier_from_elo

# ---------------------------------------------------------------------------
# Adapter protocol — the only thing the driver needs from the outside world
# ---------------------------------------------------------------------------


class RoomChannel(Protocol):
    """Subset of `MultiNamespace` that the ranked driver needs.

    Implemented for real by ``handlers.multi.events`` and faked by tests.
    """

    room_id: str

    async def emit_event(
        self,
        event: str,
        data=None,
        *,
        to: Optional[str] = None,
        skip_sid: Optional[str] = None,
    ) -> None: ...

    async def set_beatmap(
        self, *, md5: str, title: str, artist: str, version: str
    ) -> None: ...

    async def start_play(self) -> None: ...


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


@dataclass
class Player:
    uid: int
    elo_before: float
    score: int = 0  # rounds won
    bans_remaining: int = DEFAULT_BANS_PER_PLAYER
    connected: bool = False


@dataclass
class _PoolEntry:
    slot: str
    md5: str
    title: str = ""
    artist: str = ""
    version: str = ""


@dataclass
class RankedSeries:
    match_id: int
    mode: GameMode
    bo: int
    p1: Player
    p2: Player
    pool: List[_PoolEntry]
    tiebreaker: Optional[_PoolEntry]
    phase: MatchPhase = MatchPhase.WAITING
    bans: List[str] = field(default_factory=list)  # pool slots that are banned
    pick_log: List[Tuple[int, str]] = field(default_factory=list)  # (uid, slot)
    current_pick: Optional[_PoolEntry] = None
    current_round_index: int = 0
    next_picker_uid: Optional[int] = None
    next_banner_uid: Optional[int] = None
    finished: bool = False

    @property
    def win_threshold(self) -> int:
        return self.bo // 2 + 1

    @property
    def is_tied_terminal(self) -> bool:
        played = self.p1.score + self.p2.score
        return played >= self.bo and self.p1.score == self.p2.score


# ---------------------------------------------------------------------------
# Persistence helper used by the driver factory
# ---------------------------------------------------------------------------


async def materialize_pool(
    bucket: str, *, fallback: Optional[List[_PoolEntry]] = None
) -> Tuple[List[_PoolEntry], Optional[_PoolEntry]]:
    """Return ``(slots, tiebreaker)`` from ``ranked_pools`` for the given bucket.

    Anything with ``slot='TB'`` is split out as the tiebreaker.
    """
    rows = await ranked_db.pool_for_tier(bucket)
    if not rows:
        if fallback is not None:
            return fallback, None
        return [], None

    slots: List[_PoolEntry] = []
    tb: Optional[_PoolEntry] = None
    for r in rows:
        entry = _PoolEntry(
            slot=str(r["slot"]),
            md5=str(r["beatmap_md5"]),
            title=str(r.get("title") or ""),
            artist=str(r.get("artist") or ""),
            version=str(r.get("version") or ""),
        )
        if entry.slot.upper() == "TB":
            # Pick the first TB encountered; if multiple, the rest go in pool.
            if tb is None:
                tb = entry
                continue
        slots.append(entry)
    return slots, tb


# ---------------------------------------------------------------------------
# Driver class
# ---------------------------------------------------------------------------


PoolPicker = Callable[[List[_PoolEntry]], _PoolEntry]


def _default_picker(pool: List[_PoolEntry]) -> _PoolEntry:
    return random.choice(pool)


class RankedMatchDriver:
    """High-level state machine. Owns one :class:`RankedSeries`."""

    def __init__(
        self,
        series: RankedSeries,
        channel: RoomChannel,
        *,
        picker: PoolPicker = _default_picker,
    ) -> None:
        self.series = series
        self.channel = channel
        self._picker = picker
        self.on_finished: Optional[Callable[["RankedMatchDriver"], Awaitable[None]]] = (
            None
        )

    # ------------------------------------------------------------------
    # API used by the room socket handlers
    # ------------------------------------------------------------------

    async def player_connected(self, uid: int) -> None:
        s = self.series
        if uid == s.p1.uid:
            s.p1.connected = True
        elif uid == s.p2.uid:
            s.p2.connected = True
        else:
            return
        if s.p1.connected and s.p2.connected and s.phase == MatchPhase.WAITING:
            await self._enter_ban_phase()

    async def player_disconnected(self, uid: int) -> None:
        if self.series.finished:
            return
        # Treat a leave during the live series as a forfeit.
        forfeiter = uid
        winner_uid = (
            self.series.p2.uid
            if forfeiter == self.series.p1.uid
            else self.series.p1.uid
        )
        await self._finalize(winner_uid, state="forfeit")

    async def submit_ban(self, uid: int, pool_slot: str) -> bool:
        s = self.series
        if s.phase != MatchPhase.BAN:
            return False
        if s.next_banner_uid is not None and s.next_banner_uid != uid:
            return False
        actor = s.p1 if uid == s.p1.uid else s.p2 if uid == s.p2.uid else None
        if actor is None or actor.bans_remaining <= 0:
            return False
        slot_upper = pool_slot.upper()
        if slot_upper in s.bans:
            return False
        if not any(p.slot.upper() == slot_upper for p in s.pool):
            return False
        if slot_upper == "TB":
            return False
        s.bans.append(slot_upper)
        actor.bans_remaining -= 1
        await ranked_db.record_pick_or_ban(
            match_id=s.match_id,
            by_uid=uid,
            action="ban",
            pool_slot=slot_upper,
            beatmap_md5=None,
        )
        await self.channel.emit_event(
            "rankedBan",
            data={"by": str(uid), "slot": slot_upper, "bans": list(s.bans)},
        )
        if s.p1.bans_remaining == 0 and s.p2.bans_remaining == 0:
            await self._enter_pick_phase(first_picker=self._lower_elo_player().uid)
        else:
            s.next_banner_uid = self._other(uid).uid
        return True

    async def submit_pick(self, uid: int, pool_slot: str) -> bool:
        s = self.series
        if s.phase != MatchPhase.PICK:
            return False
        if s.next_picker_uid is not None and s.next_picker_uid != uid:
            return False
        slot_upper = pool_slot.upper()
        if slot_upper in s.bans:
            return False
        # find concrete pool entry
        chosen = next((p for p in s.pool if p.slot.upper() == slot_upper), None)
        if chosen is None:
            return False
        s.current_pick = chosen
        s.pick_log.append((uid, slot_upper))
        await ranked_db.record_pick_or_ban(
            match_id=s.match_id,
            by_uid=uid,
            action="pick",
            pool_slot=slot_upper,
            beatmap_md5=chosen.md5,
        )
        await self.channel.emit_event(
            "rankedPick",
            data={"by": str(uid), "slot": slot_upper, "md5": chosen.md5},
        )
        await self._start_round(chosen)
        return True

    async def round_finished(self, scores: List[Dict]) -> None:
        """Call when ``allPlayersScoreSubmitted`` fires.

        ``scores`` is a list with at least entries containing ``uid`` and
        ``score`` for both players in the order the room reported them
        (descending score). The driver advances the series accordingly.
        """
        s = self.series
        if s.phase not in (MatchPhase.ROUND, MatchPhase.TIEBREAKER):
            return
        if s.current_pick is None:
            return

        score_by_uid = self._extract_scores(scores)
        if score_by_uid is None:
            return  # malformed; ignore

        p1_score = score_by_uid.get(s.p1.uid, 0)
        p2_score = score_by_uid.get(s.p2.uid, 0)
        winner_uid = s.p1.uid if p1_score >= p2_score else s.p2.uid
        if winner_uid == s.p1.uid:
            s.p1.score += 1
        else:
            s.p2.score += 1

        s.current_round_index += 1
        await ranked_db.record_round(
            match_id=s.match_id,
            round_index=s.current_round_index,
            beatmap_md5=s.current_pick.md5,
            pool_slot=s.current_pick.slot,
            p1_score=p1_score,
            p2_score=p2_score,
            winner_uid=winner_uid,
        )
        await self.channel.emit_event(
            "rankedRoundResult",
            data={
                "round": s.current_round_index,
                "winner": str(winner_uid),
                "p1Score": p1_score,
                "p2Score": p2_score,
                "seriesScore": f"{s.p1.score}-{s.p2.score}",
            },
        )
        s.current_pick = None

        # Series end?
        if s.p1.score >= s.win_threshold:
            await self._finalize(s.p1.uid, state="finished")
            return
        if s.p2.score >= s.win_threshold:
            await self._finalize(s.p2.uid, state="finished")
            return
        if s.is_tied_terminal:
            await self._enter_tiebreaker()
            return

        # Continue picking; loser of last round picks next
        await self._enter_pick_phase(
            first_picker=s.p1.uid if winner_uid == s.p2.uid else s.p2.uid
        )

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    async def _enter_ban_phase(self) -> None:
        s = self.series
        s.phase = MatchPhase.BAN
        s.next_banner_uid = self._lower_elo_player().uid
        await self.channel.emit_event(
            "rankedPhase",
            data={
                "phase": "ban",
                "firstBanner": str(s.next_banner_uid),
                "bansPerPlayer": DEFAULT_BANS_PER_PLAYER,
                "pool": [
                    {
                        "slot": p.slot,
                        "md5": p.md5,
                        "title": p.title,
                        "artist": p.artist,
                        "version": p.version,
                    }
                    for p in s.pool
                ],
                "tiebreaker": (
                    None
                    if s.tiebreaker is None
                    else {
                        "slot": s.tiebreaker.slot,
                        "md5": s.tiebreaker.md5,
                        "title": s.tiebreaker.title,
                        "artist": s.tiebreaker.artist,
                        "version": s.tiebreaker.version,
                    }
                ),
            },
        )

    async def _enter_pick_phase(self, *, first_picker: int) -> None:
        s = self.series
        s.phase = MatchPhase.PICK
        s.next_picker_uid = first_picker
        await self.channel.emit_event(
            "rankedPhase",
            data={
                "phase": "pick",
                "picker": str(first_picker),
                "bans": list(s.bans),
                "remaining": [p.slot for p in s.pool if p.slot.upper() not in s.bans],
            },
        )

    async def _enter_tiebreaker(self) -> None:
        s = self.series
        if s.tiebreaker is None:
            # No TB configured — pick a random non-banned slot.
            available = [p for p in s.pool if p.slot.upper() not in s.bans]
            if not available:
                # Should never happen with sane pools; mark aborted.
                await self._finalize(s.p1.uid, state="aborted")
                return
            tb_pick = self._picker(available)
        else:
            tb_pick = s.tiebreaker
        s.phase = MatchPhase.TIEBREAKER
        s.current_pick = tb_pick
        await self.channel.emit_event(
            "rankedPhase",
            data={"phase": "tiebreaker", "slot": tb_pick.slot, "md5": tb_pick.md5},
        )
        await self._start_round(tb_pick)

    async def _start_round(self, pick: _PoolEntry) -> None:
        s = self.series
        if s.phase != MatchPhase.TIEBREAKER:
            s.phase = MatchPhase.ROUND
        await self.channel.set_beatmap(
            md5=pick.md5,
            title=pick.title,
            artist=pick.artist,
            version=pick.version,
        )
        await self.channel.emit_event(
            "rankedRoundStart",
            data={
                "round": s.current_round_index + 1,
                "slot": pick.slot,
                "md5": pick.md5,
            },
        )
        await self.channel.start_play()

    async def _finalize(self, winner_uid: int, *, state: str) -> None:
        s = self.series
        if s.finished:
            return
        s.finished = True
        s.phase = MatchPhase.FINISHED if state == "finished" else MatchPhase.ABORTED

        # The "won" round count for ELO weighting is the rounds won by each side.
        winner_player = s.p1 if winner_uid == s.p1.uid else s.p2
        loser_player = s.p2 if winner_uid == s.p1.uid else s.p1

        delta = update_elo(
            winner_elo=winner_player.elo_before,
            loser_elo=loser_player.elo_before,
            winner_score=winner_player.score,
            loser_score=loser_player.score,
        )
        new_winner_elo = winner_player.elo_before + delta.winner_delta
        new_loser_elo = loser_player.elo_before + delta.loser_delta

        await ranked_db.apply_match_result(
            uid=winner_player.uid,
            mode=int(s.mode),
            new_elo=new_winner_elo,
            won=True,
        )
        await ranked_db.apply_match_result(
            uid=loser_player.uid,
            mode=int(s.mode),
            new_elo=new_loser_elo,
            won=False,
        )
        p1_after = new_winner_elo if winner_uid == s.p1.uid else new_loser_elo
        p2_after = new_winner_elo if winner_uid == s.p2.uid else new_loser_elo
        await ranked_db.finalize_match(
            match_id=s.match_id,
            winner_uid=winner_uid,
            score=f"{s.p1.score}-{s.p2.score}",
            p1_elo_after=p1_after,
            p2_elo_after=p2_after,
            state=state,
        )
        await self.channel.emit_event(
            "rankedMatchFinished",
            data={
                "winner": str(winner_uid),
                "score": f"{s.p1.score}-{s.p2.score}",
                "p1Elo": p1_after,
                "p2Elo": p2_after,
                "p1Tier": tier_from_elo(p1_after).name,
                "p2Tier": tier_from_elo(p2_after).name,
                "state": state,
            },
        )
        if self.on_finished is not None:
            try:
                await self.on_finished(self)
            except Exception:  # pragma: no cover
                logging.exception("on_finished callback crashed")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _lower_elo_player(self) -> Player:
        return (
            self.series.p1
            if self.series.p1.elo_before <= self.series.p2.elo_before
            else self.series.p2
        )

    def _other(self, uid: int) -> Player:
        return self.series.p2 if uid == self.series.p1.uid else self.series.p1

    @staticmethod
    def _extract_scores(scores: List[Dict]) -> Optional[Dict[int, int]]:
        if not scores:
            return None
        out: Dict[int, int] = {}
        for entry in scores:
            uid = entry.get("uid") or entry.get("user_id") or entry.get("playerID")
            score = entry.get("score")
            if uid is None or score is None:
                continue
            try:
                out[int(uid)] = int(score)
            except (TypeError, ValueError):
                continue
        return out or None
