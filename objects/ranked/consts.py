"""Enums for ranked matches."""

from enum import IntEnum


class GameMode(IntEnum):
    """Ranked queue mode."""

    SOLO_1V1 = 1
    DUO_2V2 = 2  # planned, not part of the MVP


class MatchPhase(IntEnum):
    """Phases of a ranked best-of-N series."""

    WAITING = 0  # both players still need to connect to the room
    BAN = 1  # alternating bans (1 ban per player by default)
    PICK = 2  # alternating picks (loser of previous round picks)
    ROUND = 3  # the multiplayer round itself
    TIEBREAKER = 4  # forced TB pool slot, only when series is tied at the end
    FINISHED = 5  # series ended, ELO applied
    ABORTED = 6  # one of the players left / failed to connect / dispute


# Default best-of-N for the MVP. Can be increased to 7/9 once we hit higher
# ELO buckets (see RomAI: Bo7/Bo9 only @ 1700+).
DEFAULT_BO = 5

# Default number of bans a single player has during the BAN phase.
DEFAULT_BANS_PER_PLAYER = 1

# How long a player has to act inside ban/pick before auto-action triggers.
PHASE_TIMEOUT_SECONDS = 30

# How many seconds we wait before kicking a player from the room because they
# never connected after match was found.
ROOM_JOIN_TIMEOUT_SECONDS = 60

# How long after a disconnect we wait before declaring a technical loss.
RECONNECT_GRACE_SECONDS = 60
