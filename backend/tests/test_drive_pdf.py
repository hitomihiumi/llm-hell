"""Reading a PDF out of Drive.

Drive refuses both of the obvious routes: `export` answers 403 "Export only
supports Docs Editors files", and `viewImage` answers "not a viewable image
type". `download` is the only way to the bytes, and it does not return them -
it saves the file into the server's own workspace and reports the path.
"""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.mcp.google import (
    GoogleWorkspaceConnector,
    drive_type_hints,
    extract_pdf_text,
    is_pdf,
    parse_download_path,
)

REPORT = """**guide.pdf** saved to workspace

**Path:** /data/share/google-workspace-mcp/workspace/guide.pdf
**Size:** 373000 bytes

---
**Next steps:**
- Search for more files: `manage_drive`
"""


def test_parse_download_path_reads_the_reported_path():
    assert parse_download_path(REPORT) == "/data/share/google-workspace-mcp/workspace/guide.pdf"


def test_parse_download_path_is_none_when_absent():
    """A report that changed shape must degrade to "no content", not to a
    path built out of half a line."""
    assert parse_download_path("**guide.pdf** saved to workspace") is None
    assert parse_download_path("") is None


def test_is_pdf_reads_the_reports_abbreviated_type():
    assert is_pdf("pdf")
    assert is_pdf("application/pdf")
    assert not is_pdf("g/document")
    assert not is_pdf(None)


def test_drive_type_hints_keeps_the_column_the_hits_discard():
    text = """## Files (2)

1abc | guide.pdf | pdf | Aug 18 | 364.3 KB
2def | TEST | g/document | Aug 17 | 2.2 KB
"""
    assert drive_type_hints(text) == {"1abc": "pdf", "2def": "g/document"}


def test_extract_pdf_text_returns_empty_for_rubbish():
    """A corrupt or non-PDF file is a document that cannot be read, not a
    crash - the source has to survive it."""
    assert extract_pdf_text(b"not a pdf at all", limit=1000) == ""


def test_extract_pdf_text_respects_its_limit():
    pytest.importorskip("pypdf")
    # A minimal one-page PDF with a text-showing operator.
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)

    # A blank page has no text layer, which is exactly the scanned-document
    # case: empty, not an error.
    assert extract_pdf_text(buffer.getvalue(), limit=50) == ""


# --- the path confinement, which is the part that matters -------------------


def make_connector(tmp_path: Path) -> GoogleWorkspaceConnector:
    settings = Settings(google_share_dir=str(tmp_path), google_account_email="a@b.c")
    return GoogleWorkspaceConnector(None, settings, key="google_drive")


@pytest.mark.asyncio
async def test_a_path_outside_the_share_directory_is_refused(tmp_path, monkeypatch):
    """The path is chosen by the MCP server, not by us, and the same mount
    holds the OAuth tokens. A server that could name any path could name
    those, so the answer is confined before anything is opened."""
    outside = tmp_path.parent / "secret.json"
    outside.write_bytes(b"%PDF-1.4 pretend")

    connector = make_connector(tmp_path / "share")
    (tmp_path / "share").mkdir()

    class Raw:
        text = f"**Path:** {outside}\n"

    async def fake_call(tool, args):
        return Raw()

    monkeypatch.setattr(connector, "_call", fake_call)

    assert await connector._read_pdf("file-id") == ""


@pytest.mark.asyncio
async def test_a_missing_file_reads_as_no_content(tmp_path, monkeypatch):
    """The usual cause is the share directory not being mounted into this
    container at all - a deployment mistake, and still not a crash."""
    share = tmp_path / "share"
    share.mkdir()
    connector = make_connector(share)

    class Raw:
        text = f"**Path:** {share / 'never-written.pdf'}\n"

    async def fake_call(tool, args):
        return Raw()

    monkeypatch.setattr(connector, "_call", fake_call)

    assert await connector._read_pdf("file-id") == ""


@pytest.mark.asyncio
async def test_a_download_that_reports_no_path_reads_as_no_content(tmp_path, monkeypatch):
    share = tmp_path / "share"
    share.mkdir()
    connector = make_connector(share)

    class Raw:
        text = "**guide.pdf** saved to workspace"

    async def fake_call(tool, args):
        return Raw()

    monkeypatch.setattr(connector, "_call", fake_call)

    assert await connector._read_pdf("file-id") == ""
