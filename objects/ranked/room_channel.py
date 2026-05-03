"""Concrete :class:`RoomChannel` impl that talks to a live MultiNamespace.

Used in production by :mod:`handlers.api.ranked` after the matchmaker pairs
two players: we build a :class:`Room`, register a ``MultiNamespace`` for it,
and wrap that namespace in :class:`LiveRoomChannel`.
"""

from __future__ import annotations

from typing import Optional

from objects import glob
from objects.beatmap import Beatmap
from objects.room.consts import RoomStatus, PlayerStatus


class LiveRoomChannel:
    """RoomChannel adapter backed by an actual :class:`MultiNamespace`."""

    def __init__(self, namespace) -> None:
        self._ns = namespace

    @property
    def room_id(self) -> str:
        return self._ns.room_id

    async def emit_event(
        self,
        event: str,
        data=None,
        *,
        to: Optional[str] = None,
        skip_sid: Optional[str] = None,
    ) -> None:
        await self._ns.emit_event(event=event, data=data, to=to, skip_sid=skip_sid)

    async def set_beatmap(
        self, *, md5: str, title: str, artist: str, version: str
    ) -> None:
        room = glob.rooms.get(id=self._ns.room_id)
        if room is None:
            return
        bm = await Beatmap.from_md5(md5)
        if bm is None:
            bm = Beatmap()
            bm.md5 = md5
            bm.title = title
            bm.artist = artist
            bm.version = version
        room.map = bm
        await self.emit_event("beatmapChanged", data=bm.as_json)

    async def start_play(self) -> None:
        room = glob.rooms.get(id=self._ns.room_id)
        if room is None:
            return
        room.status = RoomStatus.PLAYING
        await self.emit_event("roomStatusChanged", data=int(room.status))
        for player in room.players:
            if player.status != PlayerStatus.NOMAP:
                player.status = PlayerStatus.PLAYING
                await self.emit_event(
                    "playerStatusChanged",
                    data=(str(player.uid), int(PlayerStatus.PLAYING)),
                )
                room.match.add_player(player)
        await self.emit_event("playBeatmap")
