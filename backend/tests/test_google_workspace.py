"""Google Workspace editor files (Docs, Sheets, Slides) are exported to PDF."""

import io

import pytest
from pypdf import PdfWriter

from app.core.config import Settings
from app.models.source import SOURCE_GOOGLE_DRIVE
from app.services.mcp.google import (
    GoogleWorkspaceConnector,
    _workspace_text_mime_type,
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
        assert arguments["range"] == "A1:Z1000"

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
    assert "print object" in content["text"]
    assert "2026-03-15" in content["text"]


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
