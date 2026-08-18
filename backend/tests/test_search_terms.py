"""What gets sent to a search backend, as opposed to what was typed.

Every source here matches its query as a literal string, so this is the
difference between a working search and an empty one. The cases below are the
ones that were actually broken: `auth-service readme` returned zero GitLab
hits while `auth-service` returned two, and a Drive document titled TEST did
not match a search for "the test document".
"""

from app.services.mcp.connector import search_terms


def test_a_question_reduces_to_its_identifying_words():
    assert search_terms("what is in the auth-service readme") == ["auth-service", "readme"]


def test_the_longest_term_comes_first():
    """The word that identifies a search is rarely a short one, and callers
    that can afford only one term take the first."""
    terms = search_terms("find the federation ranking code")

    assert terms[0] == "federation"
    assert terms == sorted(terms, key=len, reverse=True)


def test_terms_are_capped():
    terms = search_terms("please summarise the quarterly revenue forecast spreadsheet document")

    assert len(terms) == 4


def test_the_cap_is_adjustable_for_callers_that_pay_per_term():
    """Project search runs once per term and blob search once per project per
    term, so the two cannot afford the same budget."""
    assert len(search_terms("federation ranking recency boost weights", limit=1)) == 1


def test_stopwords_go_but_identifiers_stay():
    terms = search_terms("how do I find my auth-service config")

    assert "how" not in terms
    assert "find" not in terms
    assert "my" not in terms
    assert "auth-service" in terms
    assert "config" in terms


def test_casing_is_preserved():
    """Some backends match identifiers case-sensitively, so lowercasing the
    terms would quietly break a search for a class or a file name."""
    assert "SearchHit" in search_terms("where is SearchHit defined")


def test_a_question_of_nothing_but_stopwords_yields_nothing():
    """Callers must handle this rather than build an empty expression, which
    is a syntax error in Drive's query language."""
    assert search_terms("what is it") == []
    assert search_terms("") == []


def test_duplicates_appear_once():
    assert search_terms("ranking ranking ranking") == ["ranking"]


def test_non_latin_words_survive():
    """The question may be in one language and the corpus in another; the
    filter must not silently drop everything it does not recognise."""
    terms = search_terms("знайди auth-service репозиторій")

    assert "auth-service" in terms
    assert any(term == "репозиторій" for term in terms)
