"""How the image budget is spent.

Images are the expensive part of an answer prompt, and the allocation is
fiddly enough to have been wrong twice while it was being written: once
handing a whole hit everything it could render, and once breaking out of the
loop before any hit without matched pages was ever offered anything.

These pin the two properties that matter. A hit that knows *which* page
matched is served before one that can only guess, and no single hit can take
the whole budget while a lower-ranked hit that matched exactly gets nothing.
"""

import pytest

from app.schemas.search import SearchHit
from app.services.search.images import page_images, plan_pages


def hit(hit_id: str, *, source: str = "google_drive", pages: list[int] | None = None) -> SearchHit:
    return SearchHit(id=hit_id, source=source, title=hit_id, matched_pages=pages)


class Renderer:
    """Records what it was asked for and answers with one byte-string a page."""

    def __init__(self, page_count: int = 9):
        self.calls: list[tuple[str, list[int] | None, int | None]] = []
        self.page_count = page_count

    async def __call__(self, hit_id, *, pages=None, max_pages=None):
        self.calls.append((hit_id, pages, max_pages))
        chosen = pages if pages else list(range(min(max_pages or 1, self.page_count)))
        return [f"{hit_id}:{page}".encode() for page in chosen]


# --- planning ----------------------------------------------------------------


def test_every_hit_gets_its_best_page_before_any_gets_a_second():
    """A forty-page document that matched eight times must not take the whole
    budget while the hit below it, which matched once and exactly, gets
    nothing."""
    plan = plan_pages(
        [hit("greedy", pages=[1, 2, 3, 4]), hit("exact", pages=[7])],
        budget=3,
        max_per_hit=4,
    )

    assert plan["exact"] == [7]
    assert plan["greedy"] == [1, 2]


def test_the_budget_is_never_exceeded():
    plan = plan_pages(
        [hit("a", pages=[1, 2, 3]), hit("b", pages=[4, 5, 6])], budget=4, max_per_hit=3
    )

    assert sum(len(pages) for pages in plan.values()) == 4


def test_a_hit_offers_at_most_its_per_hit_cap():
    plan = plan_pages([hit("a", pages=[1, 2, 3, 4, 5])], budget=99, max_per_hit=2)

    assert plan["a"] == [1, 2]


def test_hits_without_matched_pages_are_not_planned():
    """They have no page to name. The caller asks their connector to choose."""
    plan = plan_pages([hit("a"), hit("b", pages=[3])], budget=4, max_per_hit=2)

    assert plan == {"b": [3]}


def test_no_budget_plans_nothing():
    assert plan_pages([hit("a", pages=[1])], budget=0, max_per_hit=2) == {}


# --- rendering ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_matched_pages_are_the_pages_rendered():
    renderer = Renderer()
    images = await page_images(
        [hit("a", pages=[5, 6])],
        renderer_for=lambda source: renderer,
        answer_image_hits=2,
        vision_max_pages=4,
    )

    assert renderer.calls[0][1] == [5, 6]
    assert len(images["a"]) == 2


@pytest.mark.asyncio
async def test_a_hit_with_no_matched_pages_still_gets_pictures():
    """The bug this replaced broke out of the loop before ever offering one
    anything, so a corpus with no semantic index rendered nothing at all."""
    renderer = Renderer()
    images = await page_images(
        [hit("a")],
        renderer_for=lambda source: renderer,
        answer_image_hits=2,
        vision_max_pages=3,
    )

    assert images["a"]
    # No pages named, so the connector was left to choose.
    assert renderer.calls[0][1] is None


@pytest.mark.asyncio
async def test_a_hit_that_knows_its_page_is_served_before_one_that_guesses():
    renderer = Renderer()
    images = await page_images(
        [hit("guesser"), hit("knower", pages=[2])],
        renderer_for=lambda source: renderer,
        answer_image_hits=1,
        vision_max_pages=1,
    )

    assert images["knower"] == [b"knower:2"]
    assert "guesser" not in images


@pytest.mark.asyncio
async def test_a_source_that_cannot_render_is_skipped():
    """A capability check, not a source name."""
    renderer = Renderer()

    def renderer_for(source):
        return renderer if source == "google_drive" else None

    images = await page_images(
        [hit("code", source="gitlab", pages=[1]), hit("doc", pages=[2])],
        renderer_for=renderer_for,
        answer_image_hits=2,
        vision_max_pages=2,
    )

    assert set(images) == {"doc"}


@pytest.mark.asyncio
async def test_a_renderer_that_fails_costs_its_pictures_and_nothing_else():
    """An answer written from the text alone is the previous behaviour, not a
    broken search."""

    async def exploding(hit_id, *, pages=None, max_pages=None):
        if hit_id == "bad":
            raise RuntimeError("render failed")
        return [b"ok"]

    images = await page_images(
        [hit("bad", pages=[1]), hit("good", pages=[2])],
        renderer_for=lambda source: exploding,
        answer_image_hits=2,
        vision_max_pages=2,
    )

    assert set(images) == {"good"}


@pytest.mark.asyncio
async def test_images_are_off_when_the_budget_is_zero():
    renderer = Renderer()
    images = await page_images(
        [hit("a", pages=[1])],
        renderer_for=lambda source: renderer,
        answer_image_hits=0,
        vision_max_pages=4,
    )

    assert images == {}
    assert renderer.calls == []
