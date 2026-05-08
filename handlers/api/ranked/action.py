"""``POST /api/ranked/action`` — submit a ban or a pick during a live match.

This is the only client-side input the ranked FSM needs (other than score
submission, which already flows through the existing multi event path).
The endpoint looks the active driver up by ``matchId`` in the in-memory
:data:`registry` and forwards the call.
"""

from __future__ import annotations

from quart import Blueprint, request

from objects.ranked.registry import registry
from handlers.response import ApiResponse

from ._helpers import require_key

bp = Blueprint("ranked_action", __name__)
# the auto-loader derives the URL prefix from the file path, but the script
# and the future client both expect ``/api/ranked/match/{ban,pick}`` — pin
# the prefix so we don't have to rename the file to ``handlers/api/ranked/match.py``
# (which would clash with the existing match-info module).
forced_route = "/api/ranked/match"


def _coerce(body: dict, *, allow_md5: bool = False) -> tuple[int, int, str, str]:
    match_id = int(body["matchId"])
    uid = int(body["uid"])
    slot = str(body["slot"]).strip()
    md5 = str(body.get("md5", "")).strip() if allow_md5 else ""
    return match_id, uid, slot, md5


@bp.route("/ban", methods=["POST"])
async def ban():
    body = await request.get_json(silent=True) or {}
    if not require_key(body.get("key")):
        return ApiResponse.forbidden("Invalid key.")
    try:
        match_id, uid, slot, _ = _coerce(body)
    except (KeyError, TypeError, ValueError):
        return ApiResponse.bad_request("matchId, uid, slot required.")
    driver = registry.by_match(match_id)
    if driver is None:
        return ApiResponse.not_found("Match not active.")
    ok = await driver.submit_ban(uid, slot)
    if not ok:
        return ApiResponse.bad_request("Ban rejected (wrong turn / invalid slot).")
    return ApiResponse.ok(
        {"matchId": match_id, "uid": uid, "slot": slot, "action": "ban"}
    )


@bp.route("/pick", methods=["POST"])
async def pick():
    body = await request.get_json(silent=True) or {}
    if not require_key(body.get("key")):
        return ApiResponse.forbidden("Invalid key.")
    try:
        match_id, uid, slot, _ = _coerce(body)
    except (KeyError, TypeError, ValueError):
        return ApiResponse.bad_request("matchId, uid, slot required.")
    driver = registry.by_match(match_id)
    if driver is None:
        return ApiResponse.not_found("Match not active.")
    ok = await driver.submit_pick(uid, slot)
    if not ok:
        return ApiResponse.bad_request("Pick rejected (wrong turn / invalid slot).")
    return ApiResponse.ok(
        {"matchId": match_id, "uid": uid, "slot": slot, "action": "pick"}
    )
