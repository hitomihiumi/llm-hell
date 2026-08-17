from datetime import UTC, datetime, timedelta

from app.schemas.search import SearchHit
from app.services.search.ranking import RECENCY_MAX_BOOST, fuse, recency_factor

NOW = datetime(2026, 8, 17, tzinfo=UTC)


def hit(source: str, n: int, *, timestamp=None) -> SearchHit:
    return SearchHit(id=f"{source}:{n}", source=source, title=f"{source} {n}", timestamp=timestamp)


def test_recency_factor_is_neutral_for_undated_hits():
    """Most GitLab code hits carry no timestamp. Penalising them for that
    would push an entire source down the list for a reason unrelated to
    relevance."""
    assert recency_factor(None) == 1.0


def test_recency_factor_rewards_fresh_and_decays_to_neutral():
    fresh = recency_factor(NOW, now=NOW)
    week = recency_factor(NOW - timedelta(days=7), now=NOW)
    ancient = recency_factor(NOW - timedelta(days=5000), now=NOW)

    assert fresh == 1.0 + RECENCY_MAX_BOOST
    assert 1.0 < week < fresh
    assert ancient == 1.0


def test_recency_factor_handles_naive_datetimes():
    """SQLite hands back naive datetimes even from tz-aware columns, and
    comparing naive to aware raises."""
    assert recency_factor(datetime(2026, 8, 17), now=NOW) > 1.0


def test_recency_breaks_ties_at_equal_rank():
    """At the same position within their own sources, the fresher hit wins.
    That is the whole intended effect."""
    stale = hit("a", 0, timestamp=NOW - timedelta(days=700))
    fresh = hit("b", 0, timestamp=NOW)

    ranked = fuse({"a": [stale], "b": [fresh]}, k=60, now=NOW)
    assert ranked[0] is fresh


def test_recency_cannot_overcome_more_than_one_rank_position():
    """The load-bearing property, and the reason RECENCY_MAX_BOOST is ~1/k.

    The RRF curve at k=60 is flat enough that a generous boost would let a
    fresh hit jump many places - which would make recency the primary
    ranking signal rather than a tie-breaker. A stale hit two positions
    better must still win.
    """
    stale_top = hit("a", 0, timestamp=NOW - timedelta(days=700))
    fresh = [hit("b", i, timestamp=NOW) for i in range(6)]

    ranked = fuse({"a": [stale_top], "b": fresh}, k=60, now=NOW)
    stale_index = ranked.index(stale_top)
    fresh_rank_2 = next(h for h in ranked if h.id == "b:2")

    # Beaten by at most the fresh hit one position better than it, never by
    # the one two positions worse.
    assert stale_index <= 2
    assert ranked.index(fresh_rank_2) > stale_index


def test_fuse_interleaves_sources_rather_than_concatenating():
    ranked = fuse({"a": [hit("a", i) for i in range(3)], "b": [hit("b", i) for i in range(3)]}, k=60)
    # Equal weight and equal rank means the two sources alternate, instead of
    # one source taking the whole top of the list.
    assert [h.source for h in ranked[:2]] == ["a", "b"]


def test_weights_shift_a_source_up():
    equal = fuse({"a": [hit("a", 0)], "b": [hit("b", 0)]}, k=60)
    weighted = fuse({"a": [hit("a", 0)], "b": [hit("b", 0)]}, weights={"b": 5.0}, k=60)
    assert equal[0].source == "a"
    assert weighted[0].source == "b"


def test_scores_decrease_with_rank():
    ranked = fuse({"a": [hit("a", i) for i in range(5)]}, k=60)
    scores = [h.score for h in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_in_source_is_reassigned_to_position_within_its_own_source():
    ranked = fuse({"a": [hit("a", i) for i in range(3)], "b": [hit("b", i) for i in range(3)]}, k=60)
    per_source = {}
    for h in ranked:
        per_source.setdefault(h.source, []).append(h.rank_in_source)
    assert per_source["a"] == [0, 1, 2]
    assert per_source["b"] == [0, 1, 2]


def test_total_limit_truncates():
    ranked = fuse({"a": [hit("a", i) for i in range(50)]}, total_limit=10, k=60)
    assert len(ranked) == 10


def test_per_source_cap_stops_one_source_flooding_the_list():
    """A KB query matching forty rows must not leave zero room for the two
    GitLab hits that might be the actual answer."""
    ranked = fuse(
        {"chatty": [hit("chatty", i) for i in range(40)], "quiet": [hit("quiet", 0)]},
        per_source_cap=5,
        total_limit=20,
        k=60,
    )
    assert sum(1 for h in ranked if h.source == "chatty") == 5
    assert any(h.source == "quiet" for h in ranked)


def test_empty_input_is_empty_output():
    assert fuse({}) == []
    assert fuse({"a": []}) == []


def test_ordering_is_stable_across_identical_calls():
    """Ties must not shuffle between requests, or the same query returns a
    different order each time."""
    payload = {"a": [hit("a", i) for i in range(3)], "b": [hit("b", i) for i in range(3)]}
    first = [h.id for h in fuse({k: list(v) for k, v in payload.items()}, k=60, now=NOW)]
    second = [h.id for h in fuse({k: list(v) for k, v in payload.items()}, k=60, now=NOW)]
    assert first == second
