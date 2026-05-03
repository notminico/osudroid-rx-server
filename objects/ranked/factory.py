"""Glue between the matchmaker and the rest of the server.

When the matchmaker pairs two players we have to:

1. Pick a tier-bucket pool that fits the lower of the two players' ELOs.
2. Insert a row in ``ranked_matches`` so we have an id.
3. Build a regular :class:`Room`, register a ``MultiNamespace`` and link
   it to the just-inserted match id.
4. Build the :class:`RankedMatchDriver`, attach a :class:`LiveRoomChannel`,
   and put everything in :data:`registry`.
5. Emit ``rankedMatchFound`` on a global ``/ranked`` namespace so subscribed
   clients (the queue UI) know which room to open.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from objects import glob
from objects.beatmap import Beatmap
from objects.room.consts import WinCondition
from objects.room.player import PlayerMulti
from objects.room.room import Room
from objects.room.utils import get_id

from . import db as ranked_db
from .consts import DEFAULT_BO, GameMode
from .match_driver import (
    Player,
    RankedMatchDriver,
    RankedSeries,
    materialize_pool,
)
from .registry import registry
from .room_channel import LiveRoomChannel
from .tiers import tier_from_elo


async def _build_room(match_id: int, p1_uid: int, p2_uid: int):
    """Create + register a ``Room`` + ``MultiNamespace`` for a ranked match.

    Returns ``(room, namespace)``.
    """
    # local import keeps top-level circular deps off the import graph
    from handlers.multi import sio
    from handlers.multi.main_namespace import MultiNamespace

    room = Room()
    room.id = get_id()
    room.name = f"Ranked #{match_id}"
    room.max_players = 2
    room.host = PlayerMulti.player(int(p1_uid), sid="")
    room.win_condition = WinCondition.SCOREV1
    # Ranked rooms always start without a beatmap — the driver picks one.
    room.map = Beatmap()
    room.map.md5 = ""
    room.ranked_match_id = int(match_id)
    glob.rooms.add(room)
    namespace = MultiNamespace(f"/multi/{room.id}")
    sio.register_namespace(namespace)
    return room, namespace


async def create_ranked_match(
    *,
    mode: int,
    p1_uid: int,
    p1_elo: float,
    p2_uid: int,
    p2_elo: float,
    bo: int = DEFAULT_BO,
) -> Tuple[int, str, RankedMatchDriver]:
    """Persist + spin up the in-memory state for a freshly paired match.

    Returns ``(match_id, room_id, driver)``.
    """
    match_id = int(
        await ranked_db.create_match(
            mode=int(mode),
            p1_uid=int(p1_uid),
            p2_uid=int(p2_uid),
            p1_elo=float(p1_elo),
            p2_elo=float(p2_elo),
            bo=int(bo),
        )
    )

    room, namespace = await _build_room(match_id, p1_uid, p2_uid)
    await ranked_db.attach_room(match_id, str(room.id))

    bucket = tier_from_elo(min(float(p1_elo), float(p2_elo))).pool_bucket
    pool, tb = await materialize_pool(bucket)
    if not pool:
        # Fall back to the next tier up; if still empty we let the series
        # carry on with no pool — the driver will abort gracefully when no
        # picks are available.
        for fallback_bucket in ("Silver", "Gold", "Diamond"):
            if fallback_bucket == bucket:
                continue
            pool, tb = await materialize_pool(fallback_bucket)
            if pool:
                logging.info(
                    "Ranked match %d falling back from %s pool to %s pool",
                    match_id,
                    bucket,
                    fallback_bucket,
                )
                break

    series = RankedSeries(
        match_id=match_id,
        mode=GameMode(int(mode)),
        bo=int(bo),
        p1=Player(uid=int(p1_uid), elo_before=float(p1_elo)),
        p2=Player(uid=int(p2_uid), elo_before=float(p2_elo)),
        pool=pool,
        tiebreaker=tb,
    )
    channel = LiveRoomChannel(namespace)
    driver = RankedMatchDriver(series, channel)
    registry.register(str(room.id), driver)

    async def _on_finished(_d: RankedMatchDriver) -> None:
        registry.unregister(str(room.id))

    driver.on_finished = _on_finished
    return match_id, str(room.id), driver


async def announce_match_found(
    *,
    match_id: int,
    room_id: str,
    p1_uid: int,
    p2_uid: int,
    bo: int,
) -> None:
    """Push the matched pair onto the ``/ranked`` Socket.IO namespace.

    Clients watching the queue join ``/ranked`` once they're queued; we look
    up by their UID so we only target the two paired players.
    """
    from handlers.multi import sio

    payload = {
        "matchId": int(match_id),
        "roomId": str(room_id),
        "bo": int(bo),
        "opponents": [int(p1_uid), int(p2_uid)],
    }
    try:
        await sio.emit("rankedMatchFound", payload, namespace="/ranked")
    except Exception:  # pragma: no cover
        logging.exception("Failed to broadcast rankedMatchFound")


async def matchmaker_pair_handler(a, b) -> None:
    """The handler used by :class:`Matchmaker`.

    ``a`` / ``b`` are :class:`QueueEntry` instances. Both have already been
    removed from ``ranked_queue`` by the matchmaker.
    """
    match_id, room_id, _ = await create_ranked_match(
        mode=int(a.mode),
        p1_uid=int(a.user_id),
        p1_elo=float(a.elo_at_join),
        p2_uid=int(b.user_id),
        p2_elo=float(b.elo_at_join),
    )
    await announce_match_found(
        match_id=match_id,
        room_id=room_id,
        p1_uid=int(a.user_id),
        p2_uid=int(b.user_id),
        bo=DEFAULT_BO,
    )
