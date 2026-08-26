"""Turning a PDF into something a text model can read.

Two halves, and the split matters. The text layer is exact, free and covers
most documents - it is extracted first and is never replaced by anything a
model produced. Rasterising pages and having a vision model describe them is
the expensive half, and it exists for what the text layer cannot contain:
the pinout diagram, the scanned page, the screenshot of a table.

Everything here is pure - bytes in, bytes or strings out - so the interesting
decisions can be tested without a PDF server, a GPU or a network.
"""

import io
import logging
from dataclasses import dataclass

logger = logging.getLogger("llmhell.pdf")

# 1.5 renders an A4 page to roughly 893x1263, which was the smallest size at
# which the labels on this corpus's wiring diagrams stayed legible.
DEFAULT_SCALE = 1.5

# JPEG rather than PNG, measured rather than assumed: on the dense diagram
# pages of a real datasheet, PNG came to 388-657 KB per page against 141-165
# KB for JPEG at this quality, for no visible loss of small text. On a
# sparse, text-only page the two are close and it does not matter.
DEFAULT_QUALITY = 85


@dataclass(frozen=True)
class PageStat:
    """What is cheaply knowable about a page without rendering it."""

    index: int
    text_chars: int
    image_count: int

    @property
    def visual_score(self) -> float:
        """How much of this page is likely to be *only* in the picture.

        Images alone is the wrong signal - every page of an illustrated
        datasheet has some - and so is short text, which would rank a blank
        page top. The ratio is what separates "a diagram with a caption" from
        "prose with a logo in the corner".
        """
        return self.image_count / (1 + self.text_chars / 1000)


def page_stats(data: bytes) -> list[PageStat]:
    """Per-page text length and image count, without rendering anything.

    Both come from the PDF's own structures, so this is cheap enough to run
    on every PDF and decide from the result whether to spend a GPU on it.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        logger.info("could not read PDF structure: %s", exc)
        return []

    stats: list[PageStat] = []
    for index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page should not lose the rest
            text = ""

        images = 0
        try:
            resources = page.get("/Resources") or {}
            xobjects = resources.get("/XObject")
            if xobjects is not None:
                xobjects = xobjects.get_object()
                images = sum(
                    1
                    for name in xobjects
                    if xobjects[name].get_object().get("/Subtype") == "/Image"
                )
        except Exception:  # noqa: BLE001 - an unreadable resource dict means "no images known"
            images = 0

        stats.append(PageStat(index=index, text_chars=len(text), image_count=images))
    return stats


def page_texts(data: bytes) -> list[str]:
    """The text layer, one string per page.

    `page_stats` already extracts exactly this and then throws it away,
    keeping only the length. It is kept separate rather than folded in
    because the two have different costs to a caller: stats are read for
    every PDF to decide whether to spend a GPU on it, while the text is only
    wanted when a document is being indexed page by page.

    A page that fails to parse yields an empty string rather than shifting
    every page after it by one - the index *is* the page number, and a
    silently renumbered document would attach the wrong picture to the right
    answer.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        logger.info("could not read PDF structure: %s", exc)
        return []

    texts: list[str] = []
    for page in reader.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            texts.append("")
    return texts


def select_visual_pages(stats: list[PageStat], *, max_pages: int) -> list[int]:
    """Which pages are worth showing a vision model, most promising first.

    A cap is not an optimisation here, it is the difference between a feature
    and an outage: every page of the datasheet this was built against carries
    images, so "render what has pictures" would send a 200-page manual through
    a GPU one page at a time.

    Pages with no images at all are never selected. Whatever is on them is
    already in the text layer, and asking a model to describe a page of prose
    produces a worse copy of something exact.
    """
    if max_pages <= 0:
        return []
    candidates = [stat for stat in stats if stat.image_count > 0]
    # Highest score first, and page order as the tie-break so a document with
    # uniform pages is read from the front rather than arbitrarily.
    candidates.sort(key=lambda stat: (-stat.visual_score, stat.index))
    return sorted(stat.index for stat in candidates[:max_pages])


def render_pages(
    data: bytes,
    indices: list[int],
    *,
    scale: float = DEFAULT_SCALE,
    quality: int = DEFAULT_QUALITY,
) -> dict[int, bytes]:
    """Page index -> JPEG bytes.

    pypdfium2 rather than PyMuPDF: both are one wheel with no system
    dependencies, but PyMuPDF is AGPL and this runs inside a product. PDFium
    is BSD/Apache.

    A page that fails to render is skipped rather than raising - one bad page
    should not cost the document the rest of its illustrations.
    """
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        logger.info("could not open PDF for rendering: %s", exc)
        return {}

    rendered: dict[int, bytes] = {}
    for index in indices:
        if index < 0 or index >= len(document):
            continue
        try:
            image = document[index].render(scale=scale).to_pil().convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality)
            rendered[index] = buffer.getvalue()
        except Exception as exc:  # noqa: BLE001
            logger.info("could not render page %d: %s", index + 1, exc)
    return rendered
