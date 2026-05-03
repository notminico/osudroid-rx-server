from objects.ranked.elo import (
    expected_score,
    score_diff_factor,
    update_elo,
    K_FACTOR,
)


def test_equal_ratings_have_50pct_expected_score():
    assert expected_score(1500, 1500) == 0.5


def test_higher_rated_has_above_50pct():
    assert expected_score(1700, 1500) > 0.5
    assert expected_score(1300, 1500) < 0.5


def test_score_diff_factor_clamped():
    assert score_diff_factor(0, 0) == 0
    assert 0 < score_diff_factor(500_000, 0) < 1
    assert score_diff_factor(10_000_000, 0) == 1


def test_update_elo_zero_sum():
    delta = update_elo(1500, 1500)
    assert abs(delta.winner_delta + delta.loser_delta) < 1e-9


def test_underdog_wins_more():
    underdog = update_elo(1300, 1500)
    favorite = update_elo(1500, 1300)
    assert underdog.winner_delta > favorite.winner_delta


def test_score_margin_increases_delta():
    close = update_elo(1500, 1500, winner_score=1, loser_score=1)
    blowout = update_elo(1500, 1500, winner_score=1_000_000, loser_score=0)
    assert blowout.winner_delta > close.winner_delta


def test_delta_is_clamped():
    huge = update_elo(100, 3000)
    assert huge.winner_delta <= K_FACTOR * 2
    assert huge.winner_delta > 0
