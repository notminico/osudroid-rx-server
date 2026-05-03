"""ELO-window matchmaking.

The matchmaker is a periodic asyncio task that:

1. Reads the ``ranked_queue`` table.
2. For every mode, pairs up players whose ELO are within a time-expanding
   window (200 -> 400 -> 600 over two minutes).
3. For each pair, atomically removes them from the queue and returns the pair
   to the caller, who is responsible for actually building a room.

Pure logic lives in :func:`pair_candidates`; it is unit-testable without
any database or sockets.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple

from . import db as ranked_db

# Window in ELO points that grows with how long the older candidate
# has been waiting (in seconds).
_INITIAL_WINDOW = 200.0
_WINDOW_GROWTH_PER_SECOND = 200.0 / 60.0  # +200 per minute
_MAX_WINDOW = 800.0


def matchmaking_window(waiting_seconds: float) -> float:
    """Return the current ELO window for a player who has been queued for ``waiting_seconds``."""
    return min(
        _MAX_WINDOW,
        _INITIAL_WINDOW + max(0.0, waiting_seconds) * _WINDOW_GROWTH_PER_SECOND,
    )


@dataclass(frozen=True)
class QueueEntry:
    user_id: int
    mode: int
    joined_at: int
    elo_at_join: float


def pair_candidates(
    entries: List[QueueEntry], *, now: Optional[float] = None
) -> Tuple[List[Tuple[QueueEntry, QueueEntry]], List[QueueEntry]]:
    """Greedy ELO-window matchmaking.

    Sorts by ``joined_at`` ASC (longest waiter first), then for each unmatched
    head picks the closest-ELO partner whose pair-window contains the head.

    Returns ``(pairs, leftovers)``.
    """
    if not entries:
        return [], []
    now_ts = float(now or time.time())
    pool = sorted(entries, key=lambda e: e.joined_at)
    pairs: List[Tuple[QueueEntry, QueueEntry]] = []
    leftovers: List[QueueEntry] = []
    consumed: set = set()

    for i, head in enumerate(pool):
        if head.user_id in consumed:
            continue
        head_window = matchmaking_window(now_ts - head.joined_at)

        best: Optional[QueueEntry] = None
        best_diff = float("inf")
        for cand in pool[i + 1 :]:
            if cand.user_id in consumed:
                continue
            cand_window = matchmaking_window(now_ts - cand.joined_at)
            diff = abs(head.elo_at_join - cand.elo_at_join)
            window = max(head_window, cand_window)
            if diff <= window and diff < best_diff:
                best, best_diff = cand, diff

        if best is None:
            leftovers.append(head)
        else:
            consumed.add(head.user_id)
            consumed.add(best.user_id)
            pairs.append((head, best))

    return pairs, leftovers


# ---------------------------------------------------------------------------
# DB-backed orchestration
# ---------------------------------------------------------------------------


PairHandler = Callable[[QueueEntry, QueueEntry], Awaitable[None]]


class Matchmaker:
    """Drives the queue table; emits paired :class:`QueueEntry` to a handler."""

    def __init__(self, on_pair: PairHandler) -> None:
        self._on_pair = on_pair

    async def tick(self) -> int:
        """Run one matchmaking pass for every mode in the queue.

        Returns the number of pairs created.
        """
        modes = {
            int(row["mode"])
            for row in (
                await ranked_db.queue_snapshot(1) + await ranked_db.queue_snapshot(2)
            )
        }
        pairs_made = 0
        for mode in sorted(modes):
            entries = [
                QueueEntry(
                    user_id=int(r["user_id"]),
                    mode=int(r["mode"]),
                    joined_at=int(r["joined_at"]),
                    elo_at_join=float(r["elo_at_join"]),
                )
                for r in await ranked_db.queue_snapshot(mode)
            ]
            pairs, _ = pair_candidates(entries)
            for a, b in pairs:
                # Remove both from the queue *before* invoking the handler so
                # a slow handler does not double-pair the same player.
                await ranked_db.queue_leave(a.user_id)
                await ranked_db.queue_leave(b.user_id)
                try:
                    await self._on_pair(a, b)
                    pairs_made += 1
                except Exception:  # pragma: no cover - defensive
                    logging.exception(
                        "matchmaker pair handler crashed for %s vs %s",
                        a.user_id,
                        b.user_id,
                    )
                    # Best-effort: re-queue both players.
                    await ranked_db.queue_join(a.user_id, a.mode, a.elo_at_join)
                    await ranked_db.queue_join(b.user_id, b.mode, b.elo_at_join)
        return pairs_made
