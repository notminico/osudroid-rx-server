"""End-to-end ranked match demo.

Boots two fake players, drives them through queue -> match -> ban -> pick ->
play -> ELO via the same REST + Socket.IO surface a real client would use.
Prints state diffs so you can see ELO move and the match get persisted.

Run while the server is up (``python main.py`` in another terminal):

    python scripts/e2e_demo.py

The script is idempotent — it re-uses (or creates) two fixed user ids 1001,
1002 and applies their ELO changes against whatever is already in the db.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import socketio

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from objects import glob  # noqa: E402

P1_UID = 1001
P2_UID = 1002
P1_NAME = "demo_alpha"
P2_NAME = "demo_beta"
DEFAULT_KEY = os.environ.get("DEMO_KEY", "devwlkey")
DEFAULT_HOST = os.environ.get("DEMO_HOST", "http://127.0.0.1:8080")


# ---------------------------------------------------------------------------
# DB helpers (direct, bypass REST so we don't depend on registration flow)
# ---------------------------------------------------------------------------


async def ensure_users() -> None:
    await glob.db.connect()
    for uid, name in [(P1_UID, P1_NAME), (P2_UID, P2_NAME)]:
        await glob.db.execute(
            """INSERT INTO users (id, username, username_safe, password_hash,
               status, country) VALUES ($1, $2, $3, 'demo-hash', 0, 'XX')
               ON CONFLICT (id) DO NOTHING RETURNING id""",
            [uid, name, name],
        )
        await glob.db.execute(
            "INSERT INTO stats (id) VALUES ($1) ON CONFLICT (id) DO NOTHING "
            "RETURNING id",
            [uid],
        )


async def snapshot_state(label: str) -> None:
    rows = (
        await glob.db.fetchall(
            "SELECT user_id, elo, peak_elo, wins, losses, games, tier "
            "FROM ranked_stats WHERE user_id IN ($1, $2) ORDER BY user_id",
            [P1_UID, P2_UID],
        )
        or []
    )
    queued = (
        await glob.db.fetchall(
            "SELECT user_id, mode, joined_at FROM ranked_queue "
            "WHERE user_id IN ($1, $2)",
            [P1_UID, P2_UID],
        )
        or []
    )
    matches = (
        await glob.db.fetchall(
            "SELECT id, p1_uid, p2_uid, winner_uid, score, state, "
            "p1_elo_before, p1_elo_after, p2_elo_before, p2_elo_after "
            "FROM ranked_matches WHERE p1_uid=$1 AND p2_uid=$2 "
            "ORDER BY id DESC LIMIT 3",
            [P1_UID, P2_UID],
        )
        or []
    )
    print(f"\n----- {label} -----")
    print("ranked_stats:")
    for r in rows:
        print(
            f"  uid={r['user_id']} elo={r['elo']:.1f} W={r['wins']} L={r['losses']} games={r['games']} tier={r['tier']}"
        )
    if not rows:
        print("  (no ranked_stats rows yet)")
    print("ranked_queue:")
    for r in queued:
        print(f"  uid={r['user_id']} mode={r['mode']} joined_at={r['joined_at']}")
    if not queued:
        print("  (queue empty)")
    print("ranked_matches (last 3):")
    for r in matches:
        print(
            f"  id={r['id']} p1={r['p1_uid']} p2={r['p2_uid']} state={r['state']} winner={r['winner_uid']} score={r['score']}"
        )
        print(
            f"     elo_before=({r['p1_elo_before']}, {r['p2_elo_before']}) elo_after=({r['p1_elo_after']}, {r['p2_elo_after']})"
        )
    if not matches:
        print("  (no matches yet)")


# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------


class Api:
    def __init__(self, session: aiohttp.ClientSession, host: str, key: str):
        self.session = session
        self.host = host
        self.key = key

    async def queue_join(self, uid: int) -> Dict[str, Any]:
        async with self.session.post(
            f"{self.host}/api/ranked/queue/join",
            json={"uid": uid, "mode": 1, "key": self.key},
        ) as r:
            return await r.json()

    async def ban(self, match_id: int, uid: int, slot: str) -> Dict[str, Any]:
        async with self.session.post(
            f"{self.host}/api/ranked/match/ban",
            json={"matchId": match_id, "uid": uid, "slot": slot, "key": self.key},
        ) as r:
            return await r.json()

    async def pick(self, match_id: int, uid: int, slot: str) -> Dict[str, Any]:
        async with self.session.post(
            f"{self.host}/api/ranked/match/pick",
            json={"matchId": match_id, "uid": uid, "slot": slot, "key": self.key},
        ) as r:
            return await r.json()


# ---------------------------------------------------------------------------
# Fake Socket.IO clients
# ---------------------------------------------------------------------------


class FakePlayer:
    """One Socket.IO client per namespace (the python-socketio ``AsyncClient``
    can't add namespaces after the initial handshake), so we keep a separate
    ``self.lobby`` for ``/ranked`` and ``self.room`` for ``/multi/<id>``."""

    def __init__(self, uid: int, host: str):
        self.uid = uid
        self.host = host
        self.lobby = socketio.AsyncClient(reconnection=False)
        self.room: Optional[socketio.AsyncClient] = None
        self.match_found_evt: asyncio.Event = asyncio.Event()
        self.match_id: Optional[int] = None
        self.room_id: Optional[str] = None
        self.play_beatmap_evt: asyncio.Event = asyncio.Event()
        self.match_finished_evt: asyncio.Event = asyncio.Event()
        self._events: List[Dict[str, Any]] = []

        self.lobby.on("rankedMatchFound", self._on_match_found, namespace="/ranked")

    async def connect_to_ranked_lobby(self) -> None:
        await self.lobby.connect(self.host, namespaces=["/ranked"])

    async def _on_match_found(self, data):
        if self.uid in data.get("opponents", []):
            self.match_id = int(data["matchId"])
            self.room_id = str(data["roomId"])
            self.match_found_evt.set()

    async def join_room(self) -> None:
        room_ns = f"/multi/{self.room_id}"
        self.room = socketio.AsyncClient(reconnection=False)

        @self.room.on("playBeatmap", namespace=room_ns)
        def _on_play_beatmap(*args):
            self._events.append({"event": "playBeatmap", "args": args})
            self.play_beatmap_evt.set()

        @self.room.on("rankedMatchFinished", namespace=room_ns)
        def _on_finished(data=None):
            self._events.append({"event": "rankedMatchFinished", "data": data})
            self.match_finished_evt.set()

        @self.room.on("rankedPhase", namespace=room_ns)
        def _on_phase(data=None):
            self._events.append({"event": "rankedPhase", "data": data})
            print(f"  [P{self.uid}] rankedPhase -> {data}")

        @self.room.on("rankedRoundResult", namespace=room_ns)
        def _on_round_result(data=None):
            self._events.append({"event": "rankedRoundResult", "data": data})
            print(f"  [P{self.uid}] rankedRoundResult -> {data}")

        @self.room.on("error", namespace=room_ns)
        def _on_error(data=None):
            print(f"  [P{self.uid}] room error: {data}")

        await self.room.connect(
            self.host,
            namespaces=[room_ns],
            auth={"type": "0", "uid": str(self.uid), "password": ""},
        )

    async def submit_score(self, score: int) -> None:
        assert self.room is not None
        await self.room.emit(
            "scoreSubmission",
            {
                "score": score,
                "combo": 100,
                "accuracy": 95.0,
                "isAlive": True,
                "hit300": 100,
                "hit100": 5,
                "hit50": 0,
                "hitmiss": 0,
                "hitgeki": 80,
                "hitkatsu": 5,
                "grade": "S",
                "mods": "",
            },
            namespace=f"/multi/{self.room_id}",
        )

    async def disconnect(self) -> None:
        for client in (self.room, self.lobby):
            if client is None:
                continue
            try:
                await client.disconnect()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Demo orchestration
# ---------------------------------------------------------------------------


async def wait_for(evt: asyncio.Event, timeout: float, what: str) -> None:
    try:
        await asyncio.wait_for(evt.wait(), timeout=timeout)
    except asyncio.TimeoutError as e:
        raise RuntimeError(f"Timed out waiting for {what} ({timeout}s)") from e


async def run_demo(host: str, key: str) -> None:
    print("== Setup: ensuring demo users exist in DB ==")
    await ensure_users()
    await snapshot_state("BEFORE")

    print("\n== Step 1: Both players hit /api/ranked/queue/join ==")
    async with aiohttp.ClientSession() as session:
        api = Api(session, host, key)
        # Connect both Socket.IO clients to /ranked first so we don't miss the
        # rankedMatchFound broadcast.
        p1 = FakePlayer(P1_UID, host)
        p2 = FakePlayer(P2_UID, host)
        await asyncio.gather(p1.connect_to_ranked_lobby(), p2.connect_to_ranked_lobby())

        r1 = await api.queue_join(P1_UID)
        r2 = await api.queue_join(P2_UID)
        print(f"  P1 join -> {json.dumps(r1)}")
        print(f"  P2 join -> {json.dumps(r2)}")

        print("\n== Step 2: Wait for matchmaker to pair (5s tick) ==")
        await asyncio.gather(
            wait_for(p1.match_found_evt, 30, "P1 match-found"),
            wait_for(p2.match_found_evt, 30, "P2 match-found"),
        )
        match_id = p1.match_id
        room_id = p1.room_id
        assert match_id is not None and room_id is not None
        print(f"  matched! match_id={match_id} room_id={room_id}")

        print("\n== Step 3: Both players join /multi/<roomId> ==")
        await p1.join_room()
        await p2.join_room()
        await asyncio.sleep(1.0)

        print("\n== Step 4: Ban phase (1 ban each) ==")
        # Lower-elo player bans first; both at 1000 ELO so use uid as tiebreaker.
        # Driver picks the lowest uid as banner.
        ban1 = await api.ban(match_id, P1_UID, "NM1")
        print(f"  P1 ban NM1 -> {json.dumps(ban1)}")
        ban2 = await api.ban(match_id, P2_UID, "NM2")
        print(f"  P2 ban NM2 -> {json.dumps(ban2)}")
        await asyncio.sleep(0.5)

        print("\n== Step 5: Pick + play rounds until series ends (Bo5) ==")
        # The driver is now in PICK phase.  We drive until rankedMatchFinished.
        rounds_played = 0
        scores = [
            (1_000_000, 700_000),  # P1 wins r1
            (600_000, 950_000),  # P2 wins r2
            (1_100_000, 800_000),  # P1 wins r3 -> series 2-1
            (1_200_000, 900_000),  # P1 wins r4 -> 3-1, finished
        ]
        slots_to_pick = ["HD1", "HR1", "DT1", "HD2", "HR2"]
        slot_idx = 0
        # We need to guess who picks: lowest elo picks first; thereafter the
        # round LOSER picks. We'll rely on rankedPhase events to know whose
        # turn it is. A simpler approach: try uid alternately.
        next_picker = P1_UID
        while not p1.match_finished_evt.is_set() and rounds_played < 5:
            slot = slots_to_pick[slot_idx % len(slots_to_pick)]
            slot_idx += 1
            print(f"  [round {rounds_played + 1}] picker={next_picker} slot={slot}")
            # Clear BEFORE submitting the pick — the server emits playBeatmap
            # synchronously inside the pick handler, so if we cleared after we
            # would race past it.
            p1.play_beatmap_evt.clear()
            p2.play_beatmap_evt.clear()
            r = await api.pick(match_id, next_picker, slot)
            if r.get("status") != "success":
                # retry with the other player
                other = P2_UID if next_picker == P1_UID else P1_UID
                print(f"    pick rejected, retrying as {other}")
                r = await api.pick(match_id, other, slot)
                next_picker = other
            print(f"    pick result: {json.dumps(r)}")
            await asyncio.gather(
                wait_for(
                    p1.play_beatmap_evt, 10, f"P1 playBeatmap r{rounds_played + 1}"
                ),
                wait_for(
                    p2.play_beatmap_evt, 10, f"P2 playBeatmap r{rounds_played + 1}"
                ),
            )

            s1, s2 = scores[rounds_played]
            print(f"    submitting scores: P1={s1} P2={s2}")
            await asyncio.gather(p1.submit_score(s1), p2.submit_score(s2))
            await asyncio.sleep(1.0)

            # Loser picks next round
            next_picker = P2_UID if s1 > s2 else P1_UID
            rounds_played += 1

        print("\n== Step 6: Wait for match-finished event ==")
        await wait_for(p1.match_finished_evt, 10, "match-finished")

        await p1.disconnect()
        await p2.disconnect()

    await asyncio.sleep(0.5)
    await snapshot_state("AFTER")
    await glob.db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--key", default=DEFAULT_KEY)
    args = parser.parse_args()
    asyncio.run(run_demo(args.host, args.key))


if __name__ == "__main__":
    main()
