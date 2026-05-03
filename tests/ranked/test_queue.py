from objects.ranked.queue import QueueEntry, matchmaking_window, pair_candidates


def _entry(uid: int, elo: float, joined_at: int = 0) -> QueueEntry:
    return QueueEntry(user_id=uid, mode=1, joined_at=joined_at, elo_at_join=elo)


def test_window_grows_over_time():
    assert matchmaking_window(0) == 200
    assert matchmaking_window(60) > matchmaking_window(0)
    assert matchmaking_window(10**5) <= 800


def test_no_pair_below_window():
    pairs, leftovers = pair_candidates([_entry(1, 1000), _entry(2, 5000)], now=0)
    assert pairs == []
    assert {e.user_id for e in leftovers} == {1, 2}


def test_pairs_close_elo():
    pairs, leftovers = pair_candidates(
        [_entry(1, 1000), _entry(2, 1100), _entry(3, 1500)], now=0
    )
    assert leftovers == [_entry(3, 1500)]
    assert len(pairs) == 1
    paired_ids = {p.user_id for p in pairs[0]}
    assert paired_ids == {1, 2}


def test_long_waiter_pairs_with_far_elo_eventually():
    # head waited 5 minutes, his window is at the cap (800)
    pairs, _ = pair_candidates(
        [_entry(1, 1000, joined_at=0), _entry(2, 1700, joined_at=300)],
        now=400,
    )
    assert len(pairs) == 1


def test_empty_input_safe():
    assert pair_candidates([], now=0) == ([], [])
