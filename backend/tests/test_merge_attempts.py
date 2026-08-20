"""Merging what several phrasings of one question found.

Each planned query is a full fan-out, so a source answers more than once and
those answers have to become one result before ranking sees them.
"""

from app.schemas.search import SearchHit
from app.services.mcp.connector import SourceResult
from app.services.search.service import _merge_attempts


def hit(hit_id: str, rank: int) -> SearchHit:
    return SearchHit(id=hit_id, source="google_drive", kind="document", title=hit_id, rank_in_source=rank)


def result(*hits: SearchHit, error: str | None = None, ms: int = 100, degraded: bool = False) -> SourceResult:
    return SourceResult(
        source_key="google_drive", hits=list(hits), error=error, elapsed_ms=ms, degraded=degraded
    )


def test_a_single_attempt_is_returned_untouched():
    only = result(hit("a", 0))

    assert _merge_attempts([only]) is only


def test_a_document_found_twice_keeps_its_best_rank():
    """Rank within a source is what fusion consumes, and a document that
    answered two phrasings is not less relevant than one that answered one."""
    merged = _merge_attempts([result(hit("a", 4)), result(hit("a", 0))])

    assert [h.id for h in merged.hits] == ["a"]
    assert merged.hits[0].rank_in_source == 0


def test_ranks_are_renumbered_without_holes():
    """Fusion reads position. A merged list with gaps would weight hits by an
    accident of which phrasing found them."""
    merged = _merge_attempts([result(hit("a", 0), hit("b", 7)), result(hit("c", 3))])

    assert [h.rank_in_source for h in merged.hits] == [0, 1, 2]


def test_a_source_is_failed_only_when_every_phrasing_failed():
    merged = _merge_attempts([result(error="timeout"), result(error="timeout")])

    assert not merged.ok
    assert merged.error == "timeout"


def test_one_phrasing_failing_is_degraded_not_dead():
    """The other query returned results; throwing them away because a sibling
    timed out would be the opposite of what the fan-out is for."""
    merged = _merge_attempts([result(error="timeout"), result(hit("a", 0))])

    assert merged.ok
    assert merged.degraded
    assert [h.id for h in merged.hits] == ["a"]


def test_elapsed_is_the_slowest_leg_because_they_ran_together():
    merged = _merge_attempts([result(hit("a", 0), ms=300), result(hit("b", 0), ms=900)])

    assert merged.elapsed_ms == 900


def test_the_detail_records_how_many_phrasings_ran():
    merged = _merge_attempts([result(hit("a", 0)), result(hit("b", 0))])

    assert merged.detail["queries_run"] == 2
