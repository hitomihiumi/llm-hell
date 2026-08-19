"""Which pages get shown to a vision model, and what happens to what it says.

The expensive half of reading a PDF is rendering pages and describing them, so
the decisions worth testing are the ones that avoid doing it: what counts as a
visual page, how many are allowed, and what the result is merged into.
"""

import httpx
import pytest

from app.services.llm import vision
from app.services.pdf import PageStat, select_visual_pages

# The real distribution from the datasheet this was built against: every page
# carries images, so "render what has pictures" would render everything.
DATASHEET = [
    PageStat(index=0, text_chars=741, image_count=2),
    PageStat(index=1, text_chars=2135, image_count=4),
    PageStat(index=2, text_chars=1314, image_count=11),
    PageStat(index=3, text_chars=1344, image_count=7),
    PageStat(index=4, text_chars=623, image_count=9),
]


def test_pages_without_images_are_never_selected():
    """Whatever is on a page of prose is already in the text layer, exactly.
    A model's reading of it would be a worse copy of something we have."""
    stats = [
        PageStat(index=0, text_chars=3000, image_count=0),
        PageStat(index=1, text_chars=100, image_count=1),
    ]

    assert select_visual_pages(stats, max_pages=4) == [1]


def test_the_cap_holds_when_every_page_has_pictures():
    """This is the case that makes the cap load-bearing rather than an
    optimisation - a 200-page illustrated manual would otherwise be 200 GPU
    calls."""
    assert len(select_visual_pages(DATASHEET, max_pages=4)) == 4
    assert len(select_visual_pages(DATASHEET, max_pages=2)) == 2


def test_the_most_picture_heavy_pages_win():
    """Page 1 is the text cover - two images and a paragraph of specifications
    already in the text layer - so with four slots it is the one dropped."""
    assert select_visual_pages(DATASHEET, max_pages=4) == [1, 2, 3, 4]


def test_the_score_prefers_a_diagram_over_an_illustrated_essay():
    diagram = PageStat(index=0, text_chars=100, image_count=4)
    essay = PageStat(index=1, text_chars=4000, image_count=4)

    assert diagram.visual_score > essay.visual_score
    assert select_visual_pages([essay, diagram], max_pages=1) == [0]


def test_selection_is_returned_in_page_order():
    """Chosen by score, read in order: a transcription labelled "page 5"
    appearing before "page 3" reads as a mistake even when it is not."""
    assert select_visual_pages(DATASHEET, max_pages=3) == sorted(
        select_visual_pages(DATASHEET, max_pages=3)
    )


def test_disabling_the_feature_selects_nothing():
    assert select_visual_pages(DATASHEET, max_pages=0) == []


# --- merging ---------------------------------------------------------------


def test_merge_puts_the_text_layer_first_and_labels_the_rest():
    merged = vision.merge("exact text", {2: "a wiring diagram", 0: "a cover"})

    assert merged.startswith("exact text")
    assert "[page 1, read from the page image]" in merged
    assert "[page 3, read from the page image]" in merged
    # Page order, not the order the dictionary happened to iterate in.
    assert merged.index("[page 1,") < merged.index("[page 3,")


def test_merge_survives_a_pdf_with_no_text_layer():
    """A scanned document is the case the vision model exists for: no text
    layer at all, and the transcription is the whole content."""
    merged = vision.merge("", {0: "a scanned invoice"})

    assert merged.startswith("[page 1,")
    assert "scanned invoice" in merged


def test_merge_with_nothing_described_is_just_the_text_layer():
    assert vision.merge("exact text", {}) == "exact text"


# --- the model call --------------------------------------------------------


def test_the_image_travels_as_a_data_uri():
    """The server has no route to this process's filesystem, so a page has to
    be carried in the request rather than referenced."""
    messages = vision.build_messages(b"\xff\xd8\xffnot-really-a-jpeg")
    parts = messages[0]["content"]

    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_a_page_the_model_calls_empty_is_dropped(monkeypatch):
    """"(nothing)" is the reply asked for on a blank or decorative page.
    Feeding that string into an answer prompt as if it were content is worse
    than having no transcription."""

    class Result:
        content = "(nothing)"

    async def fake_complete(*args, **kwargs):
        return Result()

    monkeypatch.setattr(vision.chat, "complete", fake_complete)

    got = await vision.describe_page(object(), b"x", http_client=httpx.AsyncClient())
    assert got == ""


@pytest.mark.asyncio
async def test_a_vision_failure_costs_the_illustration_not_the_search(monkeypatch):
    """By the time this runs the text layer is already in hand. Raising here
    would throw away a working document because one page would not render."""

    async def fake_complete(*args, **kwargs):
        raise vision.chat.ChatError("vision endpoint unreachable")

    monkeypatch.setattr(vision.chat, "complete", fake_complete)

    got = await vision.describe_pages(
        object(), {0: b"x", 1: b"y"}, http_client=httpx.AsyncClient()
    )
    assert got == {}


@pytest.mark.asyncio
async def test_pages_are_described_concurrently_and_keyed_by_index(monkeypatch):
    seen: list[int] = []

    class Result:
        def __init__(self, text):
            self.content = text

    async def fake_complete(_endpoint, messages, **kwargs):
        # The image is the only thing that differs between calls.
        url = messages[0]["content"][1]["image_url"]["url"]
        seen.append(len(url))
        return Result(f"page of {len(url)}")

    monkeypatch.setattr(vision.chat, "complete", fake_complete)

    got = await vision.describe_pages(
        object(), {3: b"aaa", 1: b"b"}, http_client=httpx.AsyncClient()
    )

    assert sorted(got) == [1, 3]
    assert len(seen) == 2
