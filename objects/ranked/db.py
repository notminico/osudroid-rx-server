"""Postgres schema and DAO helpers for the ranked module.

The DDL in :data:`DDL` is appended to ``objects.db.PostgresDB.check_database``
on connect; everything else is a thin wrapper around ``glob.db`` so that the
rest of the package can stay free of SQL.
"""

from __future__ import annotations

import time
from typing import List, Optional

from objects import glob
from .tiers import tier_from_elo

DDL = """
CREATE TABLE IF NOT EXISTS ranked_stats (
    user_id          BIGINT      NOT NULL,
    mode             SMALLINT    NOT NULL,
    elo              REAL        NOT NULL DEFAULT 1000,
    peak_elo         REAL        NOT NULL DEFAULT 1000,
    wins             INT         NOT NULL DEFAULT 0,
    losses           INT         NOT NULL DEFAULT 0,
    games            INT         NOT NULL DEFAULT 0,
    tier             TEXT        NOT NULL DEFAULT 'Silver I',
    placements_left  SMALLINT    NOT NULL DEFAULT 5,
    last_played      BIGINT,
    PRIMARY KEY (user_id, mode)
);

CREATE TABLE IF NOT EXISTS ranked_queue (
    user_id        BIGINT  PRIMARY KEY,
    mode           SMALLINT NOT NULL,
    joined_at      BIGINT   NOT NULL,
    elo_at_join    REAL     NOT NULL
);

CREATE TABLE IF NOT EXISTS ranked_matches (
    id             SERIAL PRIMARY KEY,
    mode           SMALLINT NOT NULL,
    p1_uid         BIGINT NOT NULL,
    p2_uid         BIGINT NOT NULL,
    p1_elo_before  REAL,
    p2_elo_before  REAL,
    p1_elo_after   REAL,
    p2_elo_after   REAL,
    winner_uid     BIGINT,
    score          TEXT,
    bo             SMALLINT NOT NULL DEFAULT 5,
    started_at     BIGINT,
    finished_at    BIGINT,
    room_id        TEXT,
    state          TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS ranked_rounds (
    id          SERIAL PRIMARY KEY,
    match_id    INT REFERENCES ranked_matches(id) ON DELETE CASCADE,
    round_index SMALLINT,
    beatmap_md5 TEXT,
    pool_slot   TEXT,
    p1_score    BIGINT,
    p2_score    BIGINT,
    winner_uid  BIGINT
);

CREATE TABLE IF NOT EXISTS ranked_pools (
    id           SERIAL PRIMARY KEY,
    tier         TEXT NOT NULL,
    slot         TEXT NOT NULL,
    beatmap_md5  TEXT NOT NULL,
    title        TEXT,
    artist       TEXT,
    version      TEXT
);

CREATE TABLE IF NOT EXISTS ranked_picks_bans (
    id          SERIAL PRIMARY KEY,
    match_id    INT REFERENCES ranked_matches(id) ON DELETE CASCADE,
    by_uid      BIGINT,
    action      TEXT NOT NULL,
    pool_slot   TEXT,
    beatmap_md5 TEXT,
    ts          BIGINT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


async def get_or_create_stats(uid: int, mode: int) -> dict:
    """Return ranked stats row for a user, inserting defaults if needed."""
    row = await glob.db.fetch(
        "SELECT * FROM ranked_stats WHERE user_id=$1 AND mode=$2",
        [int(uid), int(mode)],
    )
    if row:
        return row
    await glob.db.execute(
        "INSERT INTO ranked_stats (user_id, mode) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING RETURNING user_id",
        [int(uid), int(mode)],
    )
    row = await glob.db.fetch(
        "SELECT * FROM ranked_stats WHERE user_id=$1 AND mode=$2",
        [int(uid), int(mode)],
    )
    return row or {
        "user_id": int(uid),
        "mode": int(mode),
        "elo": 1000.0,
        "peak_elo": 1000.0,
        "wins": 0,
        "losses": 0,
        "games": 0,
        "tier": "Silver I",
        "placements_left": 5,
        "last_played": None,
    }


async def apply_match_result(
    *,
    uid: int,
    mode: int,
    new_elo: float,
    won: bool,
) -> dict:
    """Persist new ELO/wins/losses for a player and refresh tier."""
    stats = await get_or_create_stats(uid, mode)
    peak = max(float(stats["peak_elo"]), float(new_elo))
    tier = tier_from_elo(new_elo).name
    placements_left = max(0, int(stats["placements_left"]) - 1)
    await glob.db.execute(
        """
        UPDATE ranked_stats
           SET elo=$1, peak_elo=$2, tier=$3, placements_left=$4,
               wins=wins+$5, losses=losses+$6, games=games+1, last_played=$7
         WHERE user_id=$8 AND mode=$9
        """,
        [
            float(new_elo),
            float(peak),
            tier,
            placements_left,
            1 if won else 0,
            0 if won else 1,
            int(time.time()),
            int(uid),
            int(mode),
        ],
    )
    return await get_or_create_stats(uid, mode)


async def leaderboard(mode: int, limit: int = 50) -> List[dict]:
    rows = await glob.db.fetchall(
        """
        SELECT rs.user_id, u.username, rs.elo, rs.peak_elo, rs.wins, rs.losses,
               rs.games, rs.tier
          FROM ranked_stats rs
          JOIN users u ON u.id = rs.user_id
         WHERE rs.mode=$1
         ORDER BY rs.elo DESC
         LIMIT $2
        """,
        [int(mode), int(limit)],
    )
    return rows or []


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------


async def queue_join(uid: int, mode: int, elo: float) -> None:
    await glob.db.execute(
        """
        INSERT INTO ranked_queue (user_id, mode, joined_at, elo_at_join)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO UPDATE
            SET mode=EXCLUDED.mode,
                joined_at=EXCLUDED.joined_at,
                elo_at_join=EXCLUDED.elo_at_join
        RETURNING user_id
        """,
        [int(uid), int(mode), int(time.time()), float(elo)],
    )


async def queue_leave(uid: int) -> None:
    await glob.db.execute("DELETE FROM ranked_queue WHERE user_id=$1", [int(uid)])


async def queue_status(uid: int) -> Optional[dict]:
    return await glob.db.fetch(
        "SELECT * FROM ranked_queue WHERE user_id=$1", [int(uid)]
    )


async def queue_snapshot(mode: int) -> List[dict]:
    """All current waiters for a given mode, sorted by join time."""
    return (
        await glob.db.fetchall(
            "SELECT * FROM ranked_queue WHERE mode=$1 ORDER BY joined_at ASC",
            [int(mode)],
        )
        or []
    )


# ---------------------------------------------------------------------------
# matches / rounds / picks
# ---------------------------------------------------------------------------


async def create_match(
    *,
    mode: int,
    p1_uid: int,
    p2_uid: int,
    p1_elo: float,
    p2_elo: float,
    bo: int,
    room_id: Optional[str] = None,
) -> int:
    return await glob.db.execute(
        """
        INSERT INTO ranked_matches
            (mode, p1_uid, p2_uid, p1_elo_before, p2_elo_before, bo,
             started_at, room_id, state)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'in_progress')
        """,
        [
            int(mode),
            int(p1_uid),
            int(p2_uid),
            float(p1_elo),
            float(p2_elo),
            int(bo),
            int(time.time()),
            room_id,
        ],
    )


async def attach_room(match_id: int, room_id: str) -> None:
    await glob.db.execute(
        "UPDATE ranked_matches SET room_id=$1 WHERE id=$2",
        [str(room_id), int(match_id)],
    )


async def record_round(
    *,
    match_id: int,
    round_index: int,
    beatmap_md5: str,
    pool_slot: str,
    p1_score: int,
    p2_score: int,
    winner_uid: int,
) -> None:
    await glob.db.execute(
        """
        INSERT INTO ranked_rounds
            (match_id, round_index, beatmap_md5, pool_slot, p1_score, p2_score, winner_uid)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        [
            int(match_id),
            int(round_index),
            str(beatmap_md5),
            str(pool_slot),
            int(p1_score),
            int(p2_score),
            int(winner_uid),
        ],
    )


async def record_pick_or_ban(
    *,
    match_id: int,
    by_uid: int,
    action: str,
    pool_slot: str,
    beatmap_md5: Optional[str],
) -> None:
    await glob.db.execute(
        """
        INSERT INTO ranked_picks_bans
            (match_id, by_uid, action, pool_slot, beatmap_md5, ts)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        [
            int(match_id),
            int(by_uid),
            str(action),
            str(pool_slot),
            beatmap_md5,
            int(time.time()),
        ],
    )


async def finalize_match(
    *,
    match_id: int,
    winner_uid: int,
    score: str,
    p1_elo_after: float,
    p2_elo_after: float,
    state: str = "finished",
) -> None:
    await glob.db.execute(
        """
        UPDATE ranked_matches
           SET winner_uid=$1, score=$2, p1_elo_after=$3, p2_elo_after=$4,
               finished_at=$5, state=$6
         WHERE id=$7
        """,
        [
            int(winner_uid),
            str(score),
            float(p1_elo_after),
            float(p2_elo_after),
            int(time.time()),
            str(state),
            int(match_id),
        ],
    )


async def fetch_match(match_id: int) -> Optional[dict]:
    return await glob.db.fetch(
        "SELECT * FROM ranked_matches WHERE id=$1", [int(match_id)]
    )


async def fetch_match_rounds(match_id: int) -> List[dict]:
    return (
        await glob.db.fetchall(
            "SELECT * FROM ranked_rounds WHERE match_id=$1 ORDER BY round_index",
            [int(match_id)],
        )
        or []
    )


async def fetch_match_picks(match_id: int) -> List[dict]:
    return (
        await glob.db.fetchall(
            "SELECT * FROM ranked_picks_bans WHERE match_id=$1 ORDER BY ts",
            [int(match_id)],
        )
        or []
    )


# ---------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------


async def pool_for_tier(tier_bucket: str) -> List[dict]:
    return (
        await glob.db.fetchall(
            "SELECT * FROM ranked_pools WHERE tier=$1 ORDER BY slot, id",
            [str(tier_bucket)],
        )
        or []
    )


async def upsert_pool_entry(
    *,
    tier_bucket: str,
    slot: str,
    beatmap_md5: str,
    title: str = "",
    artist: str = "",
    version: str = "",
) -> None:
    existing = await glob.db.fetch(
        "SELECT id FROM ranked_pools WHERE tier=$1 AND slot=$2 AND beatmap_md5=$3",
        [str(tier_bucket), str(slot), str(beatmap_md5)],
    )
    if existing:
        return
    await glob.db.execute(
        """
        INSERT INTO ranked_pools (tier, slot, beatmap_md5, title, artist, version)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        [
            str(tier_bucket),
            str(slot),
            str(beatmap_md5),
            str(title),
            str(artist),
            str(version),
        ],
    )
