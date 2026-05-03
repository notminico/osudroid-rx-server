"""ELO rating math.

Loosely follows the RomAI formula (see RomAI-Proj README):

    ELO_new = ELO + (K / 2) * (win + 0.5 * (diff / c))

with ``win in {-1, 1}``. We use the classic logistic-expected-score for the
matchmaking-side prediction and a tunable diff factor to reward bigger
score gaps.
"""

from dataclasses import dataclass

# Classic chess K-factor; trimmed in half for our 0.5-step rule below.
K_FACTOR: float = 32.0

# Reference score scale used to clamp the diff factor.
# osu!std scores can balloon to millions; we scale by total max score.
_DEFAULT_DIFF_SCALE: float = 1_000_000.0

# Maximum number of rating points a single round can move.
_DELTA_CLAMP: float = 64.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """Probability that A beats B given their ELO."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def score_diff_factor(
    winner_score: int, loser_score: int, scale: float = _DEFAULT_DIFF_SCALE
) -> float:
    """Bonus modifier for the magnitude of the win.

    Returns a value in ``[0, 1]`` proportional to how far apart the two
    score totals were, capped at ``scale``.
    """
    if scale <= 0:
        return 0.0
    diff = max(0, winner_score - loser_score)
    return min(1.0, diff / scale)


@dataclass(frozen=True)
class EloDelta:
    winner_delta: float
    loser_delta: float


def update_elo(
    winner_elo: float,
    loser_elo: float,
    *,
    winner_score: int = 0,
    loser_score: int = 0,
    k: float = K_FACTOR,
) -> EloDelta:
    """Compute ELO deltas for one round.

    The base movement is ``k * (1 - expected)`` (winner gains a small amount
    against a stronger opponent, a tiny amount against a weaker one). On top
    of that we add a half-step driven by the score margin (``score_diff_factor``).

    Both deltas are mirrored — winner gains exactly what loser loses — and
    clamped to ``_DELTA_CLAMP`` so a single round can never swing too hard.
    """
    expected_w = expected_score(winner_elo, loser_elo)
    diff = score_diff_factor(winner_score, loser_score)
    delta = k * (1.0 - expected_w) + (k / 2.0) * 0.5 * diff
    delta = max(-_DELTA_CLAMP, min(_DELTA_CLAMP, delta))
    return EloDelta(winner_delta=delta, loser_delta=-delta)
