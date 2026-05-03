"""``GET /api/ranked/profile`` — ranked profile (per-mode) for one user."""

from __future__ import annotations

from quart import Blueprint, request

from objects import glob
from objects.ranked import db as ranked_db
from objects.ranked.consts import GameMode
from handlers.response import ApiResponse

from ._helpers import serialize_stats

bp = Blueprint("ranked_profile", __name__)


@bp.route("/", methods=["GET"])
async def profile():
    raw_uid = request.args.get("uid")
    if not raw_uid:
        return ApiResponse.bad_request("uid required.")
    try:
        uid = int(raw_uid)
    except (TypeError, ValueError):
        return ApiResponse.bad_request("uid must be int.")
    try:
        mode = int(request.args.get("mode", GameMode.SOLO_1V1))
    except (TypeError, ValueError):
        return ApiResponse.bad_request("mode must be int.")
    user = await glob.db.fetch(
        "SELECT id, username, country FROM users WHERE id=$1", [uid]
    )
    if user is None:
        return ApiResponse.not_found("User not found.")
    stats = await ranked_db.get_or_create_stats(uid, mode)
    payload = serialize_stats(stats)
    payload["username"] = user["username"]
    payload["country"] = user["country"]
    return ApiResponse.ok(payload)
