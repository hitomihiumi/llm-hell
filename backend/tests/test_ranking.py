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


def test_recency_orders_two_hits_from_the_same_source():
    """Within one source the dating convention is uniform, so comparing
    freshness there compares like with like. This is the only place recency is
    allowed to decide anything."""
    stale = hit("a", 0, timestamp=NOW - timedelta(days=700))
    fresh = hit("a", 1, timestamp=NOW)
    # Equal scores: each was top of a different phrasing's list.
    ranked = fuse({"a": [[stale], [fresh]]}, k=60, now=NOW)

    assert [h.id for h in ranked] == [fresh.id, stale.id]


def test_recency_does_not_decide_between_sources():
    """It used to, and that was a structural bias rather than a tie-break.

    Undated hits score 1.0 and dated ones up to 1.016; GitLab code hits carry
    no timestamp and Drive documents always do, so Drive won at equal rank on
    the strength of recording dates rather than of being relevant. Measured:
    Drive's `.env` came top for "auth-service README" that way.
    """
    undated = hit("gitlab", 0)
    dated = hit("google_drive", 0, timestamp=NOW)

    ranked = fuse({"gitlab": [[undated]], "google_drive": [[dated]]}, k=60, now=NOW)

    assert ranked[0].score == ranked[1].score
    # Broken by source key - arbitrary, but neutral, and not a reward for one
    # source happening to record timestamps.
    assert ranked[0] is undated


def test_recency_cannot_outrank_a_better_score():
    """A tie-break that can overturn a real difference in score is not a
    tie-break."""
    stale_top = hit("a", 0, timestamp=NOW - timedelta(days=700))
    middle = [hit("a", i) for i in range(1, 5)]
    fresh_lower = hit("a", 5, timestamp=NOW)

    ranked = fuse({"a": [[stale_top, *middle, fresh_lower]]}, k=60, now=NOW)

    assert ranked[0] is stale_top


def test_fuse_interleaves_sources_rather_than_concatenating():
    ranked = fuse({"a": [[hit("a", i) for i in range(3)]], "b": [[hit("b", i) for i in range(3)]]}, k=60)
    # Equal weight and equal rank means the two sources alternate, instead of
    # one source taking the whole top of the list.
    assert [h.source for h in ranked[:2]] == ["a", "b"]


def test_weights_shift_a_source_up():
    equal = fuse({"a": [[hit("a", 0)]], "b": [[hit("b", 0)]]}, k=60)
    weighted = fuse({"a": [[hit("a", 0)]], "b": [[hit("b", 0)]]}, weights={"b": 5.0}, k=60)
    assert equal[0].source == "a"
    assert weighted[0].source == "b"


def test_scores_decrease_with_rank():
    ranked = fuse({"a": [[hit("a", i) for i in range(5)]]}, k=60)
    scores = [h.score for h in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_in_source_is_reassigned_to_position_within_its_own_source():
    ranked = fuse({"a": [[hit("a", i) for i in range(3)]], "b": [[hit("b", i) for i in range(3)]]}, k=60)
    per_source = {}
    for h in ranked:
        per_source.setdefault(h.source, []).append(h.rank_in_source)
    assert per_source["a"] == [0, 1, 2]
    assert per_source["b"] == [0, 1, 2]


def test_total_limit_truncates():
    ranked = fuse({"a": [[hit("a", i) for i in range(50)]]}, total_limit=10, k=60)
    assert len(ranked) == 10


def test_per_source_cap_stops_one_source_flooding_the_list():
    """A KB query matching forty rows must not leave zero room for the two
    GitLab hits that might be the actual answer."""
    ranked = fuse(
        {"chatty": [[hit("chatty", i) for i in range(40)]], "quiet": [[hit("quiet", 0)]]},
        per_source_cap=5,
        total_limit=20,
        k=60,
    )
    assert sum(1 for h in ranked if h.source == "chatty") == 5
    assert any(h.source == "quiet" for h in ranked)


def test_empty_input_is_empty_output():
    assert fuse({}) == []
    assert fuse({"a": []}) == []
    assert fuse({"a": [[]]}) == []


def test_ordering_is_stable_across_identical_calls():
    """Ties must not shuffle between requests, or the same query returns a
    different order each time."""
    payload = {"a": [[hit("a", i) for i in range(3)]], "b": [[hit("b", i) for i in range(3)]]}
    first = [h.id for h in fuse({k: [list(a) for a in v] for k, v in payload.items()}, k=60, now=NOW)]
    second = [h.id for h in fuse({k: [list(a) for a in v] for k, v in payload.items()}, k=60, now=NOW)]
    assert first == second


# --- fusing across phrasings ------------------------------------------------


def test_agreement_across_phrasings_beats_one_lucky_first_place():
    """The reason the sum is over phrasings rather than a best-rank collapse.

    Measured with best-rank-wins: asked what the auth-service README said,
    `package.json` came first, because one phrasing happened to rank it top
    while the README hit sat at rank 1 for all of them.
    """
    lucky = hit("a", 0)
    agreed = hit("a", 1)
    # `lucky` is top of one phrasing and absent from the rest; `agreed` is
    # second in all three.
    ranked = fuse(
        {"a": [[lucky, agreed], [agreed], [agreed]]},
        k=60,
    )

    assert ranked[0] is agreed
    assert agreed.score > lucky.score


def test_a_document_found_once_still_ranks_on_its_own_merit():
    """Agreement is a bonus, not a requirement - a corpus can hold exactly one
    document that answers, and only one phrasing may find it."""
    alone = hit("a", 0)
    ranked = fuse({"a": [[alone]]}, k=60)

    assert ranked == [alone]
    assert alone.score == 1 / 60


def test_the_same_document_from_two_phrasings_appears_once():
    """It is one document. Listing it twice would also let it take two slots
    of the per-source cap."""
    first = hit("a", 0)
    again = hit("a", 0)
    assert first.id == again.id

    ranked = fuse({"a": [[first], [again]]}, k=60)

    assert len(ranked) == 1


def test_rank_in_source_is_the_best_position_any_phrasing_gave_it():
    """Kept for display and for explaining the ordering after the fact."""
    hits = [hit("a", 0), hit("a", 1)]
    ranked = fuse({"a": [[hits[1], hits[0]], [hits[0], hits[1]]]}, k=60)

    by_id = {h.id: h for h in ranked}
    assert by_id["a:0"].rank_in_source == 0
    assert by_id["a:1"].rank_in_source == 0


def test_per_source_cap_counts_documents_not_phrasings():
    """The cap is applied to the result, not to each input list - capping the
    inputs would silently change what the sum is over."""
    chatty = [hit("chatty", i) for i in range(10)]
    ranked = fuse(
        {"chatty": [chatty, chatty, chatty], "quiet": [[hit("quiet", 0)]]},
        per_source_cap=3,
        total_limit=20,
        k=60,
    )

    assert sum(1 for h in ranked if h.source == "chatty") == 3
    assert any(h.source == "quiet" for h in ranked)
