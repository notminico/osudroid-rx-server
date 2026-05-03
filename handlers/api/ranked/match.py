"""``GET /api/ranked/match`` — one match's metadata (rounds, picks, bans)."""

from __future__ import annotations

from quart import Blueprint, request

from objects.ranked import db as ranked_db
from handlers.response import ApiResponse

from ._helpers import serialize_match

bp = Blueprint("ranked_match", __name__)


@bp.route("/", methods=["GET"])
async def match():
    raw_id = request.args.get("id")
    if not raw_id:
        return ApiResponse.bad_request("id required.")
    try:
        match_id = int(raw_id)
    except (TypeError, ValueError):
        return ApiResponse.bad_request("id must be int.")

    row = await ranked_db.fetch_match(match_id)
    if row is None:
        return ApiResponse.not_found("Match not found.")
    rounds = await ranked_db.fetch_match_rounds(match_id)
    picks = await ranked_db.fetch_match_picks(match_id)
    payload = serialize_match(row)
    payload["rounds"] = [
        {
            "index": int(r["round_index"]),
            "beatmapMd5": r["beatmap_md5"],
            "poolSlot": r["pool_slot"],
            "p1Score": int(r["p1_score"]) if r["p1_score"] is not None else None,
            "p2Score": int(r["p2_score"]) if r["p2_score"] is not None else None,
            "winnerUid": int(r["winner_uid"]) if r["winner_uid"] is not None else None,
        }
        for r in rounds
    ]
    payload["picksBans"] = [
        {
            "byUid": int(p["by_uid"]),
            "action": p["action"],
            "poolSlot": p["pool_slot"],
            "beatmapMd5": p["beatmap_md5"],
            "ts": int(p["ts"]),
        }
        for p in picks
    ]
    return ApiResponse.ok(payload)
