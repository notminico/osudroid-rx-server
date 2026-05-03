"""Shared utilities for ranked HTTP endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from objects import glob


def require_key(payload_key: Optional[str]) -> bool:
    """Return True if the supplied key matches ``glob.config.wl_key``.

    The same shared secret used by ``/api/wl_add`` etc. is reused here so
    that operators do not need to add a second secret to their .env.
    """
    if not payload_key:
        return False
    return str(payload_key) == str(getattr(glob.config, "wl_key", ""))


def serialize_stats(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a raw ``ranked_stats`` row into a JSON-friendly payload."""
    return {
        "userId": int(row["user_id"]),
        "mode": int(row["mode"]),
        "elo": float(row["elo"]),
        "peakElo": float(row["peak_elo"]),
        "wins": int(row["wins"]),
        "losses": int(row["losses"]),
        "games": int(row["games"]),
        "tier": str(row["tier"]),
        "placementsLeft": int(row["placements_left"]),
        "lastPlayed": int(row["last_played"]) if row.get("last_played") else None,
    }


def serialize_match(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "mode": int(row["mode"]),
        "p1Uid": int(row["p1_uid"]),
        "p2Uid": int(row["p2_uid"]),
        "p1EloBefore": (
            float(row["p1_elo_before"]) if row["p1_elo_before"] is not None else None
        ),
        "p2EloBefore": (
            float(row["p2_elo_before"]) if row["p2_elo_before"] is not None else None
        ),
        "p1EloAfter": (
            float(row["p1_elo_after"]) if row["p1_elo_after"] is not None else None
        ),
        "p2EloAfter": (
            float(row["p2_elo_after"]) if row["p2_elo_after"] is not None else None
        ),
        "winnerUid": int(row["winner_uid"]) if row["winner_uid"] is not None else None,
        "score": row.get("score"),
        "bo": int(row["bo"]),
        "startedAt": int(row["started_at"]) if row["started_at"] is not None else None,
        "finishedAt": (
            int(row["finished_at"]) if row["finished_at"] is not None else None
        ),
        "roomId": row.get("room_id"),
        "state": str(row["state"]),
    }
