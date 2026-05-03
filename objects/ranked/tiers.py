"""Tier ladder shamelessly borrowed from RomAI.

    Candidate I-IV → Silver I-III → Gold I-III → Platinum I-III →
    Diamond I-III → Atomos I-III → Cosmic I-III → Quantum

The ladder is ELO-bucketed; each non-Quantum tier covers a 50-100 ELO band.
"""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Tier:
    name: str
    min_elo: float
    pool_bucket: str  # which 'pool tier' a player at this rank uses

    def __str__(self) -> str:
        return self.name


# fmt: off
_LADDER: List[Tier] = [
    Tier("Candidate IV",  0,    "Silver"),
    Tier("Candidate III", 600,  "Silver"),
    Tier("Candidate II",  700,  "Silver"),
    Tier("Candidate I",   800,  "Silver"),
    Tier("Silver III",    900,  "Silver"),
    Tier("Silver II",     950,  "Silver"),
    Tier("Silver I",      1000, "Silver"),
    Tier("Gold III",      1075, "Gold"),
    Tier("Gold II",       1150, "Gold"),
    Tier("Gold I",        1225, "Gold"),
    Tier("Platinum III",  1300, "Gold"),
    Tier("Platinum II",   1375, "Gold"),
    Tier("Platinum I",    1450, "Gold"),
    Tier("Diamond III",   1525, "Diamond"),
    Tier("Diamond II",    1600, "Diamond"),
    Tier("Diamond I",     1675, "Diamond"),
    Tier("Atomos III",    1750, "Diamond"),
    Tier("Atomos II",     1825, "Diamond"),
    Tier("Atomos I",      1900, "Diamond"),
    Tier("Cosmic III",    1975, "Diamond"),
    Tier("Cosmic II",     2050, "Diamond"),
    Tier("Cosmic I",      2125, "Diamond"),
    Tier("Quantum",       2200, "Diamond"),
]
# fmt: on


def tier_from_elo(elo: float) -> Tier:
    """Return the tier whose ``min_elo`` is the highest one not exceeding ``elo``."""
    current = _LADDER[0]
    for tier in _LADDER:
        if elo >= tier.min_elo:
            current = tier
        else:
            break
    return current


def all_tiers() -> List[Tier]:
    return list(_LADDER)
