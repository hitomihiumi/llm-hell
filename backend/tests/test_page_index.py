"""Our own index of pages we transcribed.

It exists because a source's index is the source's business: Drive covers a
PDF's text layer, so a term printed only inside a diagram never finds its
file. `UART3` returned nothing from Drive while the pad was legible on page
three of the document sitting in the account.

The same rows are the cache, which is why staleness matters here rather than
being a nicety - a transcription of a version that no longer exists would keep
matching searches and citing a page whose content has changed.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.search import page_index

DOC = {"source_key": "google_drive", "external_id": "file-1", "fingerprint": "373000"}

PAGE_TWO = (
    "Board top view. UART3 (T3/R3) is the pad pair nearest the USB connector "
    'and is labelled "GPS" in silkscreen. A jumper marked JP1 selects 5V or 9V.'
)
PAGE_THREE = "Motor outputs S1-S4 run along the right edge, each with an adjacent GND."


@pytest_asyncio.fixture
async def db(test_db_engine):
    maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session


async def seed(db: AsyncSession, **overrides):
    payload = {
        **DOC,
        "title": "datasheet.pdf",
        "url": "https://drive.google.com/file/d/file-1/view",
        "pages": {1: PAGE_TWO, 2: PAGE_THREE},
        **overrides,
    }
    written = await page_index.save_pages(db, **payload)
    await db.commit()
    return written


@pytest.mark.asyncio
async def test_a_term_only_in_a_picture_finds_its_document(db):
    """The whole point. "silkscreen" appears nowhere in the PDF's text layer,
    so Drive cannot match it - and this can."""
    await seed(db)

    found = await page_index.search_pages(db, "silkscreen jumper", source_key="google_drive", limit=5)

    assert len(found) == 1
    external_id, title, url, page_number, text = found[0]
    assert external_id == "file-1"
    assert title == "datasheet.pdf"
    assert url.endswith("/view")
    # Page 2 of the document, zero-based here.
    assert page_number == 1
    assert "JP1" in text


@pytest.mark.asyncio
async def test_one_card_per_document_not_one_per_page(db):
    """Five pages of the same datasheet crowding out four other files is not
    a better result list."""
    await seed(db, pages={0: "GND pad", 1: "GND rail", 2: "GND plane"})

    found = await page_index.search_pages(db, "GND", source_key="google_drive", limit=5)

    assert len(found) == 1


@pytest.mark.asyncio
async def test_the_best_matching_page_is_the_one_returned(db):
    await seed(db, pages={0: "power section", 1: "UART3 GPS silkscreen pads"})

    found = await page_index.search_pages(db, "UART3 silkscreen", source_key="google_drive", limit=5)

    assert found[0][3] == 1


@pytest.mark.asyncio
async def test_a_question_of_only_stopwords_matches_nothing(db):
    """"what is it" is not a request for the entire index."""
    await seed(db)

    assert await page_index.search_pages(db, "what is it", source_key="google_drive", limit=5) == []


@pytest.mark.asyncio
async def test_another_sources_pages_are_not_returned(db):
    await seed(db)

    assert await page_index.search_pages(db, "silkscreen", source_key="gitlab", limit=5) == []


# --- the cache half ---------------------------------------------------------


@pytest.mark.asyncio
async def test_pages_are_written_once(db):
    """A page already stored must not be described again - that is the entire
    saving."""
    assert await seed(db) == 2
    assert await seed(db) == 0


@pytest.mark.asyncio
async def test_a_blank_page_is_remembered_as_blank(db):
    """Storing the empty result is what stops a decorative page being
    rendered and sent to a GPU on every single search."""
    await seed(db, pages={0: ""})

    loaded = await page_index.load_pages(db, **DOC)
    assert loaded[0] == ""


@pytest.mark.asyncio
async def test_editing_the_document_discards_the_old_reading(db):
    """The Drive id survives an edit. A transcription of the previous version
    would keep matching searches and citing a page that has changed, and
    nothing on the card would say so."""
    await seed(db)
    await seed(db, fingerprint="999999", pages={0: "completely rewritten page"})

    old = await page_index.load_pages(db, **DOC)
    assert old == {}

    new = await page_index.load_pages(
        db, source_key="google_drive", external_id="file-1", fingerprint="999999"
    )
    assert new == {0: "completely rewritten page"}

    # And the stale text is no longer findable.
    assert await page_index.search_pages(db, "silkscreen", source_key="google_drive", limit=5) == []
