"""``POST /api/ranked/queue`` — join / leave the ranked matchmaking queue."""

from __future__ import annotations

from typing import Optional

from quart import Blueprint, request

from objects import glob
from objects.ranked import db as ranked_db
from objects.ranked.consts import GameMode
from handlers.response import ApiResponse

from ._helpers import require_key

bp = Blueprint("ranked_queue", __name__)


async def _resolve_elo(uid: int, mode: int) -> float:
    stats = await ranked_db.get_or_create_stats(uid, mode)
    return float(stats["elo"])


@bp.route("/join", methods=["POST"])
async def join():
    body = await request.get_json(silent=True) or {}
    if not require_key(body.get("key")):
        return ApiResponse.forbidden("Invalid key.")
    try:
        uid = int(body["uid"])
        mode = int(body.get("mode", GameMode.SOLO_1V1))
    except (KeyError, TypeError, ValueError):
        return ApiResponse.bad_request("uid required (int).")
    if mode not in (GameMode.SOLO_1V1, GameMode.DUO_2V2):
        return ApiResponse.bad_request("Unsupported mode.")
    user = await glob.db.fetch("SELECT id FROM users WHERE id=$1", [uid])
    if user is None:
        return ApiResponse.not_found("User not found.")
    elo = await _resolve_elo(uid, mode)
    await ranked_db.queue_join(uid, mode, elo)
    status = await ranked_db.queue_status(uid)
    return ApiResponse.ok(
        {"queued": True, "uid": uid, "mode": mode, "joinedAt": int(status["joined_at"])}
    )


@bp.route("/leave", methods=["POST"])
async def leave():
    body = await request.get_json(silent=True) or {}
    if not require_key(body.get("key")):
        return ApiResponse.forbidden("Invalid key.")
    try:
        uid = int(body["uid"])
    except (KeyError, TypeError, ValueError):
        return ApiResponse.bad_request("uid required (int).")
    await ranked_db.queue_leave(uid)
    return ApiResponse.ok({"queued": False, "uid": uid})


@bp.route("/status", methods=["GET"])
async def status():
    raw_uid: Optional[str] = request.args.get("uid")
    if not raw_uid:
        return ApiResponse.bad_request("uid required.")
    try:
        uid = int(raw_uid)
    except (TypeError, ValueError):
        return ApiResponse.bad_request("uid must be int.")
    row = await ranked_db.queue_status(uid)
    if row is None:
        return ApiResponse.ok({"queued": False, "uid": uid})
    return ApiResponse.ok(
        {
            "queued": True,
            "uid": uid,
            "mode": int(row["mode"]),
            "joinedAt": int(row["joined_at"]),
            "eloAtJoin": float(row["elo_at_join"]),
        }
    )
