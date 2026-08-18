"""Google Markdown-report parsing, driven by responses captured from a live
server (google-workspace-mcp v4.2.1), with identifiers replaced.

These are the shapes that matter: this server never returns JSON. The sibling
`test_adapters_google.py` keeps the JSON branch honest for a future version
that populates `structuredContent`, but everything here is what actually
arrives today.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.source import SOURCE_GOOGLE_DRIVE, SOURCE_GOOGLE_MAIL
from app.services.mcp.google import (
    drive_hits_from_markdown,
    drive_query,
    drive_url,
    email_hits_from_markdown,
    extract_report_body,
    gmail_query,
    parse_markdown_rows,
    parse_short_date,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"


def fixture_text(name: str) -> str:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return "".join(
        block.get("text", "") for block in data["content_blocks"] if block.get("type") == "text"
    )


# --- the real Drive search response -----------------------------------------

DRIVE_SEARCH = """## Files (1)

1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcde | TEST | g/document | Aug 17 | 2.2 KB

---
**Next steps:**
- Get file details: `manage_drive` — `{"operation":"get","email":"demo@example.com","fileId":"<id from results>"}`

---
**Session context** (demo@example.com):
- No new unread emails since session start (2 unread, 2 today)
"""

EMAIL_SEARCH = """## Messages (2)

18f0aaaa0000bbb1 | Gmail Team <mail-noreply@goog… | Tips for using your new inbox | Aug 17
18f0aaaa0000bbb2 | Gmail Team <mail-noreply@goog… | Get the official Gmail app | Aug 17

---
**Next steps:**
- Read a specific email: `manage_email` — `{"operation":"read","email":"demo@example.com","messageId":"<id>"}`
"""


def test_drive_search_from_the_real_response():
    hits = drive_hits_from_markdown(DRIVE_SEARCH, source_key=SOURCE_GOOGLE_DRIVE, debug=False)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.external_id == "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcde"
    assert hit.title == "TEST"
    assert hit.kind == "document"
    # A Google Doc gets its editor URL, not the generic Drive file viewer.
    assert hit.url == (
        "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcde/edit"
    )
    # Search carries no body text; enrichment supplies it later.
    assert hit.snippet == ""


def test_email_search_from_the_real_response():
    hits = email_hits_from_markdown(EMAIL_SEARCH, source_key=SOURCE_GOOGLE_MAIL, debug=False)
    assert len(hits) == 2
    assert hits[0].external_id == "18f0aaaa0000bbb1"
    assert hits[0].title == "Tips for using your new inbox"
    assert hits[0].author.startswith("Gmail Team")
    assert hits[0].url.endswith("#all/18f0aaaa0000bbb1")


def test_boilerplate_after_the_rule_is_not_parsed_as_results():
    """The "Next steps" and "Session context" blocks contain ids and emails of
    their own, and pipes inside their JSON examples. Parsing past the `---`
    would invent hits out of the server's own help text."""
    hits = drive_hits_from_markdown(DRIVE_SEARCH, source_key=SOURCE_GOOGLE_DRIVE, debug=False)
    assert all("operation" not in hit.title for hit in hits)
    assert len(hits) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No files found.",
        'No messages found for query: "test".',
        "## Files (0)\n\n---\n**Next steps:**\n",
        "utter nonsense",
    ],
)
def test_empty_and_unparseable_reports_yield_no_hits(text):
    assert drive_hits_from_markdown(text, source_key=SOURCE_GOOGLE_DRIVE, debug=False) == []
    assert email_hits_from_markdown(text, source_key=SOURCE_GOOGLE_MAIL, debug=False) == []


def test_rows_missing_columns_are_skipped_not_fatal():
    text = "## Files (3)\n\nonlyid\nid | name\nid2 | name2 | g/document | Aug 1 | 1 KB\n"
    hits = drive_hits_from_markdown(text, source_key=SOURCE_GOOGLE_DRIVE, debug=False)
    # "onlyid" has no pipe at all and no name; the other two are usable.
    assert [hit.title for hit in hits] == ["name", "name2"]


def test_parse_markdown_rows_requires_the_heading():
    assert parse_markdown_rows("a | b | c") == []


# --- URLs -------------------------------------------------------------------

@pytest.mark.parametrize(
    "type_hint,expected_host_path",
    [
        ("g/document", "docs.google.com/document/d/ID/edit"),
        ("g/spreadsheet", "docs.google.com/spreadsheets/d/ID/edit"),
        ("g/presentation", "docs.google.com/presentation/d/ID/edit"),
        ("g/folder", "drive.google.com/drive/folders/ID"),
        ("application/pdf", "drive.google.com/file/d/ID/view"),
        ("", "drive.google.com/file/d/ID/view"),
    ],
)
def test_drive_url_per_type(type_hint, expected_host_path):
    """Opening a Google Doc at the generic /file/d/ viewer works badly; each
    Google type has its own editor URL."""
    assert drive_url("ID", type_hint).endswith(expected_host_path)


# --- dates ------------------------------------------------------------------

def test_short_date_with_an_explicit_year():
    assert parse_short_date("Aug 17, 2024") == datetime(2024, 8, 17, tzinfo=UTC)


def test_short_date_without_a_year_is_not_dated_into_the_future():
    """The server omits the year for recent items. Assuming the current year
    unconditionally would date a December item into the future and hand it a
    recency boost it has not earned."""
    parsed = parse_short_date("Dec 25")
    assert parsed is not None
    assert parsed <= datetime.now(UTC).replace(hour=23, minute=59)


@pytest.mark.parametrize("value", ["", "yesterday", "2026-08-17", "Foo 17", "Aug", "Aug 99"])
def test_unparseable_dates_return_none_rather_than_raising(value):
    assert parse_short_date(value) is None


# --- query building ---------------------------------------------------------

def test_drive_query_wraps_in_fulltext_contains():
    """A bare phrase is a syntax error in Drive's query language, not a
    search."""
    assert drive_query("fusion") == "(fullText contains 'fusion') and trashed = false"


def test_drive_query_ors_the_terms_rather_than_matching_a_phrase():
    """`fullText contains 'test document'` is an exact-phrase match, so a
    document titled TEST full of prose does not match it. Asking for "the test
    document" returned nothing while the file sat there in plain sight."""
    built = drive_query("test document")

    assert " or " in built
    assert "fullText contains 'test'" in built
    assert "fullText contains 'document'" in built


def test_drive_query_drops_stopwords_and_caps_the_terms():
    built = drive_query("please find me the report about the quarterly revenue forecast")

    assert "fullText contains 'the'" not in built
    assert "fullText contains 'please'" not in built
    # Four terms means four clauses, and one `or` fewer than that.
    assert built.count("fullText contains") == 4


def test_drive_query_excludes_trashed_files():
    """A deleted document offered as a source is worse than no source."""
    assert drive_query("fusion").endswith("and trashed = false")


def test_drive_query_escapes_quotes_and_backslashes():
    assert "fullText contains 'it\\'s'" in drive_query("it's")
    assert "fullText contains 'a\\\\b'" in drive_query("a\\b")


def test_drive_query_never_emits_an_empty_expression():
    """An empty expression is a syntax error, so a query of nothing but
    stopwords or whitespace still has to produce something runnable."""
    assert drive_query("   ") == "trashed = false"
    assert "fullText contains" in drive_query("the and of")


def test_gmail_query_ors_the_terms_of_a_question():
    """Gmail ANDs bare terms, so a whole question needs every one of its
    words in the same message and matches nothing. `{a b}` is Gmail's OR."""
    built = gmail_query("what does the Gmail team say about the inbox")

    assert built.startswith("{") and built.endswith("}")
    assert "Gmail" in built
    assert "inbox" in built
    assert "what" not in built


def test_gmail_query_leaves_an_expert_query_alone():
    """A query using Gmail's own operators was written by someone who knows
    the syntax; reducing it to terms would search for the word "from"."""
    assert gmail_query("from:alice budget") == "from:alice budget"
    assert gmail_query("is:unread") == "is:unread"
    assert gmail_query("subject:deploy after:2026/01/01") == "subject:deploy after:2026/01/01"


def test_gmail_query_does_not_brace_a_single_term():
    assert gmail_query("inbox") == "inbox"


def test_gmail_query_strips_double_quotes():
    """An unbalanced quote breaks the whole expression."""
    assert '"' not in gmail_query('say "hello"')


# --- body extraction --------------------------------------------------------

DOCS_GET = """## TEST

**Document ID:** 1AbCdEfG
**Revision:** REDACTED
**Length:** 3358 characters, 5 line(s)

---

Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Mauris a metus ipsum.

---
**Next steps:**
- Download this file
"""

EMAIL_READ = """## Tips for using your new inbox

**From:** Gmail Team <mail-noreply@google.com>
**To:** Demo User <demo@example.com>
**Date:** Mon, 17 Aug 2026 04:37:07 -0700
**Labels:** UNREAD, INBOX

Tips for using your new inbox

Welcome to your inbox
"""


def test_body_extraction_for_a_doc_where_the_body_follows_a_rule():
    body = extract_report_body(DOCS_GET)
    assert body.startswith("Lorem ipsum")
    assert "Document ID" not in body
    assert "Next steps" not in body


def test_body_extraction_for_an_email_where_the_body_follows_the_headers():
    """The two shapes differ - email has no `---` before the body - which is
    why this cannot simply split on the rule."""
    body = extract_report_body(EMAIL_READ)
    assert "Welcome to your inbox" in body
    assert "**From:**" not in body
    assert "Labels" not in body


@pytest.mark.parametrize("text", ["", "## Only a heading", "**Key:** value only", "---\n---\n"])
def test_body_extraction_returns_empty_rather_than_raising(text):
    assert extract_report_body(text) == ""


# --- against the saved fixtures ---------------------------------------------

def test_captured_drive_search_fixture_parses():
    text = fixture_text("google_drive_search.json")
    hits = drive_hits_from_markdown(text, source_key=SOURCE_GOOGLE_DRIVE, debug=False)
    assert len(hits) == 1
    assert hits[0].title == "TEST"


def test_captured_docs_get_fixture_yields_the_document_text():
    body = extract_report_body(fixture_text("google_docs_get.json"))
    assert "Lorem ipsum" in body
    assert "Revision" not in body


def test_captured_email_search_fixture_parses():
    hits = email_hits_from_markdown(
        fixture_text("google_email_search_hits.json"), source_key=SOURCE_GOOGLE_MAIL, debug=False
    )
    assert len(hits) == 2
    assert all(hit.url and hit.external_id for hit in hits)
