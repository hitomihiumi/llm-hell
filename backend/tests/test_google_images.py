"""Google Drive image files are sent to the answer model, not just PDF pages."""

import pytest
from PIL import Image

from app.core.config import Settings
from app.models.source import SOURCE_GOOGLE_DRIVE
from app.services.mcp.google import (
    GoogleWorkspaceConnector,
    _image_as_jpeg,
    is_image,
    is_pdf,
)


def _png_bytes(width: int = 100, height: int = 100) -> bytes:
    buffer = __import__("io").BytesIO()
    image = Image.new("RGBA", (width, height), (255, 0, 0, 255))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_is_image_recognises_common_hints():
    assert is_image("image/png")
    assert is_image("png")
    assert is_image("image/jpeg")
    assert is_image("jpg")
    assert is_image("image/webp")
    assert not is_image("application/pdf")
    assert not is_image("pdf")
    assert not is_image("g/document")
    assert not is_image(None)


def test_pdf_hints_are_not_images():
    assert is_pdf("pdf")
    assert not is_image("pdf")


def test_image_as_jpeg_produces_jpeg_bytes():
    jpeg = _image_as_jpeg(_png_bytes())
    assert jpeg is not None
    assert jpeg.startswith(b"\xff\xd8")


def test_image_as_jpeg_downscales_huge_images():
    jpeg = _image_as_jpeg(_png_bytes(3000, 4000))
    assert jpeg is not None
    # PIL does not expose dimensions from bytes without reopening.
    converted = Image.open(__import__("io").BytesIO(jpeg))
    assert max(converted.width, converted.height) <= 2048


@pytest.mark.asyncio
async def test_page_images_returns_jpeg_for_an_image_file(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)
    png = _png_bytes()

    async def _type_of(_file_id: str):
        return "image/png"

    async def _download_file(_file_id: str):
        return png

    monkeypatch.setattr(connector, "_type_of", _type_of)
    monkeypatch.setattr(connector, "_download_file", _download_file)

    pages = await connector.page_images(f"{SOURCE_GOOGLE_DRIVE}:file-1")

    assert len(pages) == 1
    assert pages[0].startswith(b"\xff\xd8")


@pytest.mark.asyncio
async def test_page_images_still_returns_empty_for_unknown_types(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)

    async def _type_of(_file_id: str):
        return "application/zip"

    async def _download_file(_file_id: str):
        return b"not an image"

    monkeypatch.setattr(connector, "_type_of", _type_of)
    monkeypatch.setattr(connector, "_download_file", _download_file)

    pages = await connector.page_images(f"{SOURCE_GOOGLE_DRIVE}:file-1")
    assert pages == []


@pytest.mark.asyncio
async def test_fetch_content_returns_preview_for_an_image_file(monkeypatch):
    connector = GoogleWorkspaceConnector(None, Settings(), key=SOURCE_GOOGLE_DRIVE)
    png = _png_bytes()

    async def _type_of(_file_id: str):
        return "image/png"

    async def _download_file(_file_id: str):
        return png

    monkeypatch.setattr(connector, "_type_of", _type_of)
    monkeypatch.setattr(connector, "_download_file", _download_file)

    content = await connector.fetch_content(f"{SOURCE_GOOGLE_DRIVE}:file-1")

    assert content is not None
    assert content["text"] == ""
    assert content["preview_pages"] == 1
