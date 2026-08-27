"""Choosing which page pictures go into the answer prompt.

Images are the expensive part of a prompt, so there is a budget, and the whole
question is how to spend it. It used to be spent a whole hit at a time: the
first two hits that could render anything took everything, however little
their pages had to do with the question, and a third hit whose retriever knew
exactly which page answered it got nothing at all.

The budget is spent on *pages* now. Each hit offers its best matched page,
then its second, and the rounds continue until the budget is gone - so a
highly ranked hit gets more pages than a low one without ever starving it.

**A hit that knows which page matched is served before one that does not**,
and that is the point rather than a tie-break. `matched_pages` comes from the
semantic index, whose chunks record the page they were cut from; a lexical hit
matches a document and not a place in it, so all its connector can offer is
`select_visual_pages` - the pages with the most ink, chosen without ever
seeing the question.

Kept apart from the routes because both `/api/search` and the coding provider
need it, and because the allocation is fiddly enough to be worth testing
without a registry, a database or a network.
"""

import logging
from typing import Any, Protocol

from app.schemas.search import SearchHit

logger = logging.getLogger("llmhell.search.images")


class PageRenderer(Protocol):
    async def __call__(
        self, hit_id: str, *, max_pages: int | None = ..., pages: list[int] | None = ...
    ) -> list[bytes]: ...


def plan_pages(
    hits: list[SearchHit], *, budget: int, max_per_hit: int
) -> dict[str, list[int]]:
    """Which pages of which hits to render, within the budget.

    Only hits carrying `matched_pages` appear here - a hit without them has no
    page to name, and is handled by the caller asking its connector to choose.

    Round-robin by rank: every hit gets its best page before any hit gets its
    second. A forty-page document that matched eight times cannot take the
    whole budget while the hit ranked below it, which matched once and
    exactly, gets nothing.
    """
    if budget <= 0:
        return {}

    offers = [(hit.id, list(hit.matched_pages or [])[:max_per_hit]) for hit in hits]
    offers = [(hit_id, pages) for hit_id, pages in offers if pages]

    plan: dict[str, list[int]] = {}
    spent = 0
    for round_index in range(max_per_hit):
        if spent >= budget:
            break
        for hit_id, pages in offers:
            if spent >= budget:
                break
            if round_index >= len(pages):
                continue
            plan.setdefault(hit_id, []).append(pages[round_index])
            spent += 1
    return plan


async def page_images(
    hits: list[SearchHit],
    *,
    renderer_for: Any,
    answer_image_hits: int,
    vision_max_pages: int,
) -> dict[str, list[bytes]]:
    """Rendered pages, keyed by hit id, for the hits about to be answered from.

    One model, one pass: these go into the answer prompt beside the text
    results rather than being described first by a second model. Nothing sits
    between the picture and the answer, so nothing a transcriber failed to
    mention can be lost.

    `renderer_for(source)` returns something callable as
    `page_images(hit_id, pages=..., max_pages=...)`, or None. A capability
    check rather than a source name: today only Drive renders pages, and the
    next source that can will be used without editing this function.

    Failures are swallowed - an answer written from the text alone is the
    previous behaviour, not a broken search.
    """
    max_per_hit = max(1, vision_max_pages)
    budget = answer_image_hits * max_per_hit
    if answer_image_hits <= 0 or budget <= 0:
        return {}

    candidates: list[tuple[SearchHit, PageRenderer]] = []
    for hit in hits:
        renderer = renderer_for(hit.source)
        if renderer is not None:
            candidates.append((hit, renderer))

    planned = plan_pages(
        [hit for hit, _ in candidates], budget=budget, max_per_hit=max_per_hit
    )
    spent = sum(len(pages) for pages in planned.values())

    images: dict[str, list[bytes]] = {}
    for hit, renderer in candidates:
        chosen = planned.get(hit.id)
        if chosen is None:
            if hit.matched_pages:
                # It named pages and the budget ran out before reaching them.
                continue
            remaining = budget - spent
            if remaining <= 0:
                continue
            take = min(remaining, max_per_hit)
        else:
            take = len(chosen)

        try:
            rendered = await renderer(hit.id, pages=chosen, max_pages=take)
        except Exception as exc:  # noqa: BLE001 - pictures are a bonus
            logger.warning("could not render pages of %s: %s", hit.id, exc)
            continue
        if rendered:
            images[hit.id] = rendered
            if chosen is None:
                spent += len(rendered)
    return images
