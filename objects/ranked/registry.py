"""Singleton-ish registry that ties room IDs to live :class:`RankedMatchDriver`s.

Used by:

* :mod:`handlers.api.ranked` — creates a driver when matchmaker pairs players.
* :mod:`handlers.multi.events.match` — looks up the driver to wire score
  submissions into the ranked state machine.
"""

from __future__ import annotations

from typing import Dict, Iterator, Optional

from .match_driver import RankedMatchDriver


class _RankedRegistry:
    def __init__(self) -> None:
        self._by_room: Dict[str, RankedMatchDriver] = {}
        self._by_match: Dict[int, RankedMatchDriver] = {}
        self._by_uid: Dict[int, RankedMatchDriver] = {}

    def register(self, room_id: str, driver: RankedMatchDriver) -> None:
        self._by_room[str(room_id)] = driver
        self._by_match[int(driver.series.match_id)] = driver
        self._by_uid[int(driver.series.p1.uid)] = driver
        self._by_uid[int(driver.series.p2.uid)] = driver

    def unregister(self, room_id: str) -> None:
        driver = self._by_room.pop(str(room_id), None)
        if driver is None:
            return
        self._by_match.pop(int(driver.series.match_id), None)
        for uid in (driver.series.p1.uid, driver.series.p2.uid):
            existing = self._by_uid.get(int(uid))
            if existing is driver:
                self._by_uid.pop(int(uid), None)

    def by_room(self, room_id: str) -> Optional[RankedMatchDriver]:
        return self._by_room.get(str(room_id))

    def by_match(self, match_id: int) -> Optional[RankedMatchDriver]:
        return self._by_match.get(int(match_id))

    def by_uid(self, uid: int) -> Optional[RankedMatchDriver]:
        return self._by_uid.get(int(uid))

    def all(self) -> Iterator[RankedMatchDriver]:
        return iter(self._by_room.values())


registry = _RankedRegistry()
