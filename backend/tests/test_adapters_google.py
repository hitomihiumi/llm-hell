"""Google Drive and Gmail result mapping.

Unlike the GitLab and Postgres adapter tests, these fixtures are SYNTHETIC -
built from the shapes of the Drive and Gmail REST APIs the MCP server wraps,
because probing the real server needs OAuth credentials this repository does
not have. See docs/google-workspace-setup.md, which asks whoever completes
the OAuth flow to capture a real response.

So the emphasis here is different: rather than pinning one known-correct
shape, these tests pin that the adapter copes with *several plausible*
shapes and degrades safely on anything it does not recognise.
"""

import pytest

from app.core.config import Settings
from app.models.source import SOURCE_GOOGLE_DRIVE, SOURCE_GOOGLE_MAIL
from app.services.mcp.connector import SearchContext
from app.services.mcp.google import GoogleWorkspaceConnector, drive_hit, email_hit, items_from


def ctx(debug: bool = False) -> SearchContext:
    return SearchContext(db=None, user=None, http_client=None, debug=debug)  # type: ignore[arg-type]


def connector(key: str = SOURCE_GOOGLE_DRIVE, **overrides) -> GoogleWorkspaceConnector:
    return GoogleWorkspaceConnector(None, Settings(**overrides), key=key)


# --- unwrapping -------------------------------------------------------------

@pytest.mark.parametrize(
    "payload,expected_count",
    [
        ([{"id": "1"}, {"id": "2"}], 2),
        ({"files": [{"id": "1"}]}, 1),
        ({"messages": [{"id": "1"}, {"id": "2"}]}, 2),
        ({"items": [{"id": "1"}]}, 1),
        ({"results": [{"id": "1"}]}, 1),
        # A single object rather than a collection.
        ({"id": "1", "name": "solo.txt"}, 1),
    ],
)
def test_items_from_handles_plausible_wrappers(payload, expected_count):
    assert len(items_from(payload)) == expected_count


@pytest.mark.parametrize("payload", [None, {}, "", 0, [], {"nothing": "useful"}, {"files": "not a list"}])
def test_items_from_returns_empty_for_anything_unrecognised(payload):
    assert items_from(payload) == []


# --- Drive ------------------------------------------------------------------

def test_drive_hit_uses_web_view_link_when_present():
    hit = drive_hit(
        {
            "id": "1AbC",
            "name": "Onboarding.docx",
            "webViewLink": "https://docs.google.com/document/d/1AbC/edit",
            "modifiedTime": "2026-08-01T10:00:00Z",
            "owners": [{"displayName": "Kate", "emailAddress": "kate@example.com"}],
            "description": "How to get access",
        },
        0,
        source_key=SOURCE_GOOGLE_DRIVE,
        debug=False,
    )
    assert hit is not None
    assert hit.kind == "document"
    assert hit.title == "Onboarding.docx"
    assert hit.url == "https://docs.google.com/document/d/1AbC/edit"
    assert hit.author == "Kate"
    assert hit.timestamp is not None
    assert hit.snippet == "How to get access"


def test_drive_hit_synthesises_a_url_from_the_id_when_no_link_is_returned():
    """Ids come back far more reliably than links, and an id is enough."""
    hit = drive_hit({"id": "9xyz", "name": "Notes"}, 0, source_key=SOURCE_GOOGLE_DRIVE, debug=False)
    assert hit.url == "https://drive.google.com/file/d/9xyz/view"


@pytest.mark.parametrize("link_field", ["webViewLink", "alternateLink", "webContentLink", "url", "link"])
def test_drive_hit_accepts_any_of_the_plausible_link_field_names(link_field):
    """The exact field name is unverified, so the adapter tries several. If it
    guessed one and were wrong, every hit would silently lose its link."""
    hit = drive_hit(
        {"id": "1", "name": "x", link_field: "https://example.com/doc"},
        0,
        source_key=SOURCE_GOOGLE_DRIVE,
        debug=False,
    )
    assert hit.url == "https://example.com/doc"


def test_drive_hit_handles_a_dict_valued_author():
    hit = drive_hit(
        {"id": "1", "name": "x", "lastModifyingUser": {"displayName": "Anna"}},
        0,
        source_key=SOURCE_GOOGLE_DRIVE,
        debug=False,
    )
    assert hit.author == "Anna"


def test_drive_hit_without_a_title_or_id_is_dropped():
    assert drive_hit({}, 0, source_key=SOURCE_GOOGLE_DRIVE, debug=False) is None


def test_drive_hit_falls_back_to_the_id_for_a_title():
    hit = drive_hit({"id": "abc"}, 0, source_key=SOURCE_GOOGLE_DRIVE, debug=False)
    assert hit is not None
    assert "abc" in hit.title


# --- Gmail ------------------------------------------------------------------

def test_email_hit_from_flattened_fields():
    hit = email_hit(
        {
            "id": "msg1",
            "subject": "Re: deployment",
            "from": "anna@example.com",
            "snippet": "The open-file limit was the problem",
            "date": "2026-08-10T09:30:00Z",
        },
        0,
        source_key=SOURCE_GOOGLE_MAIL,
        debug=False,
    )
    assert hit.kind == "email"
    assert hit.title == "Re: deployment"
    assert hit.author == "anna@example.com"
    assert hit.url == "https://mail.google.com/mail/u/0/#all/msg1"
    assert hit.timestamp is not None


def test_email_hit_from_gmail_payload_headers():
    """Gmail's own API nests these in payload.headers rather than flattening
    them, so both shapes have to work."""
    hit = email_hit(
        {
            "id": "msg2",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Quarterly review"},
                    {"name": "From", "value": "kate@example.com"},
                    {"name": "Date", "value": "2026-07-01T08:00:00Z"},
                ]
            },
        },
        0,
        source_key=SOURCE_GOOGLE_MAIL,
        debug=False,
    )
    assert hit.title == "Quarterly review"
    assert hit.author == "kate@example.com"
    assert hit.timestamp is not None


def test_email_hit_handles_a_dict_valued_sender():
    hit = email_hit(
        {"id": "m", "subject": "s", "from": {"emailAddress": "x@example.com"}},
        0,
        source_key=SOURCE_GOOGLE_MAIL,
        debug=False,
    )
    assert hit.author == "x@example.com"


def test_email_hit_without_subject_or_id_is_dropped():
    assert email_hit({}, 0, source_key=SOURCE_GOOGLE_MAIL, debug=False) is None


# --- adversarial ------------------------------------------------------------

@pytest.mark.parametrize("builder", [drive_hit, email_hit])
@pytest.mark.parametrize(
    "item",
    [
        {"id": None},
        {"name": None},
        {"id": "1", "name": "x", "modifiedTime": "not a date"},
        {"id": "1", "name": "x", "owners": "not a list"},
        {"id": "1", "name": "x", "owners": []},
        {"id": "1", "subject": "x", "payload": {"headers": "not a list"}},
        {"id": "1", "subject": "x", "payload": "not a dict"},
    ],
)
def test_builders_never_raise_on_malformed_items(builder, item):
    """A source returning an unexpected shape must contribute nothing, not
    break the whole federated search."""
    result = builder(item, 0, source_key="google_drive", debug=False)
    assert result is None or result.title


def test_unparseable_timestamp_leaves_the_field_empty_rather_than_failing():
    hit = drive_hit(
        {"id": "1", "name": "x", "modifiedTime": "yesterday-ish"},
        0,
        source_key=SOURCE_GOOGLE_DRIVE,
        debug=False,
    )
    assert hit is not None
    assert hit.timestamp is None


# --- connector wiring -------------------------------------------------------

def test_unsupported_surface_is_rejected_at_construction():
    with pytest.raises(ValueError):
        GoogleWorkspaceConnector(None, Settings(), key="google_calendar")


def test_account_is_omitted_rather_than_sent_empty():
    """An empty `email` would override the server's own default account
    with nothing."""
    assert connector(google_account_email="")._base_args() == {}
    assert connector(google_account_email="a@b.c")._base_args() == {"email": "a@b.c"}


async def test_search_reports_a_transport_failure_without_raising():
    result = await connector(google_mcp_url="http://127.0.0.1:1/mcp").search("q", limit=5, ctx=ctx())
    assert result.error is not None
    assert result.hits == []
    assert result.source_key == SOURCE_GOOGLE_DRIVE


async def test_health_warns_when_no_account_is_configured():
    report = await connector(
        google_account_email="", google_mcp_url="http://127.0.0.1:1/mcp"
    ).health()
    assert "warning" in report
    assert report["ok"] is False
