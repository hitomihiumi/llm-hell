"""Centring an excerpt on the matched passage.

Drive and Postgres return no match highlight of any kind, so without this the
reader is shown a document's opening paragraph to explain a hit that was
really about page five.
"""

import pytest

from app.services.mcp.connector import excerpt_around, truncate

LOREM = (
    "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi. " * 6
    + "The open-file limit is what the data-parallel coordinator runs out of first. "
    + "Rho sigma tau upsilon phi chi psi omega. " * 6
)


def test_excerpt_contains_the_match_that_a_head_truncation_would_miss():
    """The load-bearing case: the term sits well past the first 400
    characters, so the old behaviour showed none of it."""
    head = truncate(LOREM, 400)
    assert "open-file limit" not in head

    excerpt = excerpt_around(LOREM, "open-file limit", 400)
    assert "open-file limit" in excerpt


def test_short_text_is_returned_whole_without_ellipses():
    text = "A short row body."
    assert excerpt_around(text, "row", 400) == text


def test_interior_excerpt_is_marked_on_both_sides():
    excerpt = excerpt_around(LOREM, "coordinator", 200)
    assert excerpt.startswith("…")
    assert excerpt.endswith("…")


def test_a_match_at_the_very_start_has_no_leading_ellipsis():
    text = "Reciprocal rank fusion. " + ("padding words here. " * 60)
    excerpt = excerpt_around(text, "reciprocal", 200)
    assert not excerpt.startswith("…")
    assert excerpt.startswith("Reciprocal")


def test_falls_back_to_the_head_when_nothing_matches():
    """Never worse than before: a hit whose terms live in the title rather
    than the body still gets its old excerpt."""
    excerpt = excerpt_around(LOREM, "nonexistent-term", 400)
    assert excerpt == truncate(LOREM, 400)


@pytest.mark.parametrize("query", ["", "   ", "a", "of in at", "!!! ???"])
def test_queries_with_no_usable_terms_fall_back(query):
    """Two-letter fragments match almost everywhere and would anchor the
    excerpt at random, so they are ignored."""
    assert excerpt_around(LOREM, query, 400) == truncate(LOREM, 400)


def test_window_prefers_where_the_most_distinct_terms_appear():
    text = (
        "fusion " * 40  # one term repeated, but only one
        + "here is where reciprocal rank fusion is actually explained together. "
        + "padding " * 40
    )
    excerpt = excerpt_around(text, "reciprocal rank fusion", 300)
    assert "reciprocal rank fusion is actually explained" in excerpt


def test_result_respects_the_budget():
    # The two ellipses are the only allowed overshoot.
    excerpt = excerpt_around(LOREM, "coordinator", 200)
    assert len(excerpt) <= 202


def test_matching_is_case_insensitive():
    text = "padding " * 60 + "The Open-File Limit matters. " + "padding " * 60
    assert "Open-File Limit" in excerpt_around(text, "open-file limit", 200)


def test_excerpt_does_not_begin_or_end_mid_word():
    excerpt = excerpt_around(LOREM, "coordinator", 200).strip("…").strip()
    assert LOREM.find(excerpt) != -1, "excerpt is not a clean slice of the source"
    start = LOREM.find(excerpt)
    assert start == 0 or LOREM[start - 1] == " "
    end = start + len(excerpt)
    assert end == len(LOREM) or LOREM[end] == " "


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_input_yields_empty_output(text):
    assert excerpt_around(text, "anything", 400) == ""


def test_whitespace_is_collapsed_like_truncate():
    text = "one\n\ntwo\t\tthree   four " + ("pad " * 200)
    assert "one two three four" in excerpt_around(text, "three", 200)


def test_a_single_enormous_word_does_not_break_it():
    """No spaces means no word boundary to snap to; must degrade rather than
    return an empty string."""
    text = "x" * 5000
    result = excerpt_around(text, "xxx", 200)
    assert result
    assert len(result) <= 202


def test_regex_metacharacters_in_the_query_are_literal():
    """A query is user input, not a pattern - `c++` must not blow up the
    term matcher."""
    text = "padding " * 60 + "we still write some c++ here " + "padding " * 60
    assert excerpt_around(text, "c++", 200)
