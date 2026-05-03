from objects.ranked.tiers import all_tiers, tier_from_elo


def test_below_floor_is_lowest_candidate():
    assert tier_from_elo(0).name == "Candidate IV"


def test_silver_band():
    assert tier_from_elo(1000).name == "Silver I"
    assert tier_from_elo(1000).pool_bucket == "Silver"


def test_gold_band():
    assert tier_from_elo(1100).name == "Gold III"
    assert tier_from_elo(1100).pool_bucket == "Gold"


def test_quantum_is_top():
    assert tier_from_elo(99999).name == "Quantum"


def test_ladder_is_monotonic():
    ladder = all_tiers()
    for prev, current in zip(ladder, ladder[1:]):
        assert prev.min_elo < current.min_elo
