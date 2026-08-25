"""Google Workspace editor files (Docs, Sheets, Slides) are exported to PDF."""

import io

import pytest
from pypdf import PdfWriter

from app.core.config import Settings
from app.models.source import SOURCE_GOOGLE_DRIVE
from app.schemas.search import SearchHit
from app.services.mcp.google import (
    GoogleWorkspaceConnector,
    _scopes,
    _token_is_valid,
    _workspace_text_mime_type,
    _worth_reading,
    drive_size_hints,
    is_google_doc,
    is_google_spreadsheet,
    is_google_workspace_editor,
    is_previewable_drive_file,
    parse_drive_size,
)


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_workspace_types_are_recognised():
    assert is_google_workspace_editor("g/spreadsheet")
    assert is_google_workspace_editor("g/document")
    assert is_google_workspace_editor("g/presentation")
    assert is_google_workspace_editor("application/vnd.google-apps.spreadsheet")
    assert not is_google_workspace_editor("pdf")
    assert not is_google_workspace_editor("image/png")


def test_google_doc_detection():
    assert is_google_doc("g/document")
    assert is_google_doc("application/vnd.google-apps.document")
    assert not is_google_doc("g/spreadsheet")


def test_previewable_drive_file_covers_workspace_editors():
    assert is_previewable_drive_file("g/document")
    assert is_previewable_drive_file("pdf")
    assert is_previewable_drive_file("image/png")
    assert not is_previewable_drive_file("g/folder")


@pytest.mark.asyncio
async def test_page_images_skips_spreadsheets_to_avoid_export_limit(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)

    async def _type_of(_file_id: str):
        return "g/spreadsheet"

    monkeypatch.setattr(connector, "_type_of", _type_of)

    pages = await connector.page_images(f"{SOURCE_GOOGLE_DRIVE}:sheet-1")

    # No PDF export attempted for spreadsheets; they are read via manage_sheets.
    assert pages == []


@pytest.mark.asyncio
async def test_fetch_content_returns_text_and_preview_for_a_doc(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)

    async def _type_of(_file_id: str):
        return "g/document"

    async def _export_file_as_pdf(_file_id: str):
        return _pdf_bytes()

    async def _call(tool, _arguments):
        class Raw:
            text = "## Title\n\n---\n\nThis is the body text.\n\n---\n**Next steps:**"

        return Raw()

    monkeypatch.setattr(connector, "_type_of", _type_of)
    monkeypatch.setattr(connector, "_export_file_as_pdf", _export_file_as_pdf)
    monkeypatch.setattr(connector, "_call", _call)
    monkeypatch.setattr(
        "app.services.mcp.google.select_visual_pages", lambda _stats, *, max_pages: [0]
    )

    content = await connector.fetch_content(f"{SOURCE_GOOGLE_DRIVE}:doc-1")

    assert content is not None
    assert content["preview_pages"] == 1
    assert "body text" in content["text"]


def test_workspace_text_mime_type():
    assert _workspace_text_mime_type("g/spreadsheet") == "text/csv"
    assert _workspace_text_mime_type("g/document") == "text/plain"
    assert _workspace_text_mime_type("g/presentation") is None


def test_spreadsheet_detection():
    assert is_google_spreadsheet("g/spreadsheet")
    assert is_google_spreadsheet("application/vnd.google-apps.spreadsheet")
    assert not is_google_spreadsheet("g/document")


@pytest.mark.asyncio
async def test_fetch_content_reads_sheet_via_manage_sheets(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)

    async def _type_of(_file_id: str):
        return "g/spreadsheet"

    async def _call(tool, arguments):
        assert tool == "manage_sheets"
        assert arguments["operation"] == "read"
        assert arguments["range"] == "A1:AZ1000"

        class Raw:
            text = "R1: task | status | date\nR2: print object | done | 2026-03-15"

        return Raw()

    async def _export_file_as_pdf(_file_id: str):
        return None

    monkeypatch.setattr(connector, "_type_of", _type_of)
    monkeypatch.setattr(connector, "_call", _call)
    monkeypatch.setattr(connector, "_export_file_as_pdf", _export_file_as_pdf)

    content = await connector.fetch_content(f"{SOURCE_GOOGLE_DRIVE}:sheet-1")

    assert content is not None
    assert content["preview_pages"] == 0
    # Addressed rather than raw: the viewer shows the same cells, with the
    # same column letters, that the answer model was given. See services/sheets.
    assert "R2: A=print object  B=done  C=2026-03-15" in content["text"]


@pytest.mark.asyncio
async def test_fetch_content_falls_back_to_text_when_pdf_export_fails(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)

    async def _type_of(_file_id: str):
        return "g/spreadsheet"

    async def _export_file_as_pdf(_file_id: str):
        return None

    async def _export_workspace_text(_file_id: str, _type_hint: str | None):
        return b"name,value\nfoo,42\nbar,7"

    monkeypatch.setattr(connector, "_type_of", _type_of)
    monkeypatch.setattr(connector, "_export_file_as_pdf", _export_file_as_pdf)
    monkeypatch.setattr(connector, "_export_workspace_text", _export_workspace_text)

    content = await connector.fetch_content(f"{SOURCE_GOOGLE_DRIVE}:big-sheet-1")

    assert content is not None
    assert content["preview_pages"] == 0
    assert "foo,42" in content["text"]
    assert "bar,7" in content["text"]


def test_parse_drive_size_understands_units():
    assert parse_drive_size("**Size:** 2.2 KB") == 2252
    assert parse_drive_size("**Size:** 15 MB") == 15 * 1024 * 1024
    assert parse_drive_size("**Size:** 1 GB") == 1024 * 1024 * 1024
    assert parse_drive_size("**Size:** unknown") is None


def test_drive_size_hints_parse_search_report():
    report = "## Files (2)\n\nid1 | name | g/document | Aug 20 | 2.2 KB\nid2 | name | g/spreadsheet | Aug 20 | 15 MB"
    sizes = drive_size_hints(report)
    assert sizes == {"id1": 2252, "id2": 15 * 1024 * 1024}


@pytest.mark.asyncio
async def test_pdf_export_is_skipped_for_large_files(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)
    connector._file_sizes["large-doc-1"] = 50 * 1024 * 1024

    calls = []

    async def _export_file(_file_id: str, mime_type: str):
        calls.append(mime_type)
        return None

    monkeypatch.setattr(connector, "_export_file", _export_file)

    result = await connector._export_file_as_pdf("large-doc-1")

    assert result is None
    assert calls == []  # Never attempted because the file is too large.


# --- which hits are worth a second round trip --------------------------------


def _hit(title: str, rank: int):
    return SearchHit(id=f"google_drive:{rank}", source=SOURCE_GOOGLE_DRIVE, title=title, rank_in_source=rank)


def test_a_file_the_question_names_is_read_first():
    """Drive put three weekly progress reports above the spreadsheet actually
    called "Gantt Chart", so the one file the question pointed at was never
    opened - it reached the answer as a title with an empty snippet."""
    hits = [
        _hit("Copy of Week 8 Progress Report", 0),
        _hit("Copy of Week 9 Progress Report", 1),
        _hit("Week 8 Progress Report", 2),
        _hit("Wisco Wingmen Gantt Chart", 3),
    ]

    chosen = _worth_reading(hits, "according to the gantt chart when was the team object 3d printed", 3)

    assert chosen[0].title == "Wisco Wingmen Gantt Chart"


def test_rank_still_decides_when_no_title_matches():
    """Nothing clever when there is nothing to be clever about: Drive's own
    order is the best guess available."""
    hits = [_hit("alpha", 0), _hit("beta", 1), _hit("gamma", 2)]

    chosen = _worth_reading(hits, "something else entirely", 2)

    assert [hit.title for hit in chosen] == ["alpha", "beta"]


def test_only_as_many_as_the_budget_allows():
    hits = [_hit(f"file {index}", index) for index in range(10)]

    assert len(_worth_reading(hits, "file", 3)) == 3


# --- reading an account's status ------------------------------------------------
#
# Verbatim from a running v4.2.1, because both of the bugs these cover came
# from guessing at the format rather than looking at it.

CONNECTED = """## Account Status: vlad@borzo.ai

[x] Token valid
[x] Has refresh token
**Scopes (12):**
- userinfo.email
- calendar
- documents
- drive
- gmail.modify
- openid

---
**Next steps:**
- Refresh credentials: `manage_accounts`
"""

NEVER_CONNECTED = """## Account Status: nobody@example.com

[ ] Token invalid
[ ] No refresh token
**Scopes (0):**
(no scopes)

---
**Next steps:**
- Refresh credentials: `manage_accounts`
"""


def test_an_account_with_credentials_reads_as_connected():
    assert _token_is_valid(CONNECTED) is True


def test_an_account_without_them_does_not():
    """The bug this exists for: the first version asked whether the report
    contained "valid", which is true of "invalid" too - so an address that
    had never authenticated reported as connected, and searching as it
    returned an empty Drive with no explanation."""
    assert _token_is_valid(NEVER_CONNECTED) is False


def test_an_access_token_without_a_refresh_token_is_not_connected():
    """It works for an hour and then stops, which is worse than failing now."""
    partial = CONNECTED.replace("[x] Has refresh token", "[ ] No refresh token")

    assert _token_is_valid(partial) is False


def test_nothing_at_all_is_not_connected():
    assert _token_is_valid("") is False


def test_the_granted_scopes_are_read():
    """Also from guessing: the first version looked for full
    `https://www.googleapis.com/auth/...` URLs and the server prints short
    names, so twelve scopes were reported as none."""
    scopes = _scopes(CONNECTED)

    assert "drive" in scopes
    assert "gmail.modify" in scopes
    assert len(scopes) == 6


def test_an_account_with_no_scopes_has_none_rather_than_a_placeholder():
    assert _scopes(NEVER_CONNECTED) == []


# --- begin_account_auth: the message a user actually gets ---------------------
#
# The bug this replaced: the message pointed at
# `docker compose exec google-mcp node .../build/index.js`, which cannot open
# a window - it starts a bare MCP stdio server with nothing driving it, inside
# a container with no display. Both assertions below exist because of that:
# the broken command must never come back, and the real one must.


@pytest.mark.asyncio
async def test_an_unauthenticated_account_is_pointed_at_a_machine_that_has_a_display(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)

    async def account_status(_email):
        return {"authenticated": False, "message": "not connected", "scopes": []}

    monkeypatch.setattr(connector, "account_status", account_status)

    result = await connector.begin_account_auth("nobody@example.com")

    assert result["authenticated"] is False
    assert "docker compose exec google-mcp" not in result["message"]
    assert "npx -y @modelcontextprotocol/inspector google-workspace-mcp" in result["message"]
    assert "nobody@example.com" in result["message"]


@pytest.mark.asyncio
async def test_missing_oauth_credentials_are_called_out_before_anything_else(monkeypatch):
    """Running the workstation flow with no client id/secret fails at a
    later, more confusing step. Saying so here is a step earlier."""
    connector = GoogleWorkspaceConnector(None, Settings(google_client_id="", google_client_secret=""), key=SOURCE_GOOGLE_DRIVE)

    async def account_status(_email):
        return {"authenticated": False, "message": "not connected", "scopes": []}

    monkeypatch.setattr(connector, "account_status", account_status)

    result = await connector.begin_account_auth("nobody@example.com")

    assert "GOOGLE_CLIENT_ID" in result["message"]


@pytest.mark.asyncio
async def test_an_already_authenticated_account_is_not_asked_to_reconnect(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)

    async def account_status(_email):
        return {"authenticated": True, "message": "ok", "scopes": ["drive", "gmail.modify"]}

    monkeypatch.setattr(connector, "account_status", account_status)

    result = await connector.begin_account_auth("vlad@borzo.ai")

    assert result["authenticated"] is True
    assert "vlad@borzo.ai" in result["message"]
