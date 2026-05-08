#!/usr/bin/env bash
# Print a compact snapshot of the four ranked tables. Used by the e2e demo
# to compare BEFORE vs AFTER state in the recording.
set -euo pipefail
LABEL="${1:-snapshot}"

echo
echo "============================================================"
echo "  $LABEL — ranked DB snapshot"
echo "============================================================"
docker exec -i romai-pg psql -U rxuser -d rxserver \
    -c "SELECT user_id AS uid, elo, peak_elo, wins, losses, games, tier FROM ranked_stats ORDER BY user_id;" \
    -c "SELECT user_id AS uid, mode, joined_at, elo_at_join FROM ranked_queue ORDER BY joined_at;" \
    -c "SELECT id, p1_uid, p2_uid, state, winner_uid, score, p1_elo_before, p1_elo_after, p2_elo_before, p2_elo_after, room_id FROM ranked_matches ORDER BY id;" \
    -c "SELECT match_id, round_index, pool_slot, p1_score, p2_score, winner_uid FROM ranked_rounds ORDER BY match_id, round_index;" \
    -c "SELECT match_id, action, pool_slot, by_uid FROM ranked_picks_bans ORDER BY match_id, id;"
echo "============================================================"
echo
