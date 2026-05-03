"""``GET /api/ranked/leaderboard`` — top-N ranked players for a mode."""

from __future__ import annotations

from quart import Blueprint, request

from objects.ranked import db as ranked_db
from objects.ranked.consts import GameMode
from handlers.response import ApiResponse

bp = Blueprint("ranked_leaderboard", __name__)


@bp.route("/", methods=["GET"])
async def leaderboard():
    try:
        mode = int(request.args.get("mode", GameMode.SOLO_1V1))
    except (TypeError, ValueError):
        return ApiResponse.bad_request("mode must be int.")
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    rows = await ranked_db.leaderboard(mode, limit=limit)
    payload = [
        {
            "userId": int(row["user_id"]),
            "username": row["username"],
            "elo": float(row["elo"]),
            "peakElo": float(row["peak_elo"]),
            "wins": int(row["wins"]),
            "losses": int(row["losses"]),
            "games": int(row["games"]),
            "tier": str(row["tier"]),
        }
        for row in rows
    ]
    return ApiResponse.ok(payload)
