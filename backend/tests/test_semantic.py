"""The semantic index: chunking, storing, and searching by meaning.

Everything here runs against a fake embedder. The point is not to check that
a neural network embeds well - it is to check the code around it, which is
where the failures that matter live: a document edited and indexed twice, two
models' vectors compared as if they were the same, a long document taking
every result slot.
"""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.document_chunk import DocumentChunk
from app.services.search import semantic
from app.services.search.embeddings import cosine


def settings(**overrides) -> Settings:
    return Settings(**{"embeddings_model": "fake-model", "semantic_min_score": 0.0, **overrides})


class FakeEmbedder:
    """A deterministic stand-in for the embedding server.

    Every text becomes a vector of character-class counts, which is enough to
    make similar strings similar and different ones different - all the code
    under test needs from a vector - without a model, a container or a
    network.
    """

    def __init__(self, dims: int = 4):
        self.dims = dims
        self.calls: list[list[str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        import json

        inputs = json.loads(body)["input"]
        self.calls.append(inputs)
        data = []
        for index, text in enumerate(inputs):
            lowered = text.lower()
            vector = [
                float(lowered.count("a") + 1),
                float(lowered.count("e") + 1),
                float(len(text) % 7 + 1),
                float(lowered.count("z") + 1),
            ][: self.dims]
            data.append({"index": index, "embedding": vector})
        return httpx.Response(200, json={"data": data})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest_asyncio.fixture
async def db(test_db_engine):
    maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session


# --- chunking ---------------------------------------------------------------


def test_a_short_document_is_one_chunk():
    assert semantic.chunk("a short note") == ["a short note"]


def test_an_empty_document_produces_nothing():
    assert semantic.chunk("") == []
    assert semantic.chunk("   \n  ") == []


def test_a_long_document_is_cut_into_overlapping_pieces():
    text = "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(10))

    pieces = semantic.chunk(text, size=500, overlap=100)

    assert len(pieces) > 1
    assert all(len(piece) <= 500 for piece in pieces)


def test_chunks_prefer_a_paragraph_break():
    """A passage cut mid-sentence embeds as something slightly other than what
    it says."""
    text = "first paragraph, reasonably long and complete.\n\n" + "second " * 200

    pieces = semantic.chunk(text, size=120, overlap=20)

    assert pieces[0] == "first paragraph, reasonably long and complete."


def test_chunking_always_terminates_on_pathological_input():
    """A separator search that found a break at position zero could otherwise
    leave `start` where it was and loop forever."""
    pieces = semantic.chunk("\n" * 50 + "text", size=10, overlap=9)

    assert pieces


# --- fingerprints -----------------------------------------------------------


def test_an_edit_changes_the_fingerprint():
    """A document edited in Drive keeps its id, so without this the index
    would keep answering from a version that no longer exists."""
    assert semantic.fingerprint("hello") != semantic.fingerprint("hello there")


# --- indexing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_indexing_stores_a_row_per_chunk(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        stored = await semantic.index_document(
            db,
            source_key="google_drive",
            external_id="doc1",
            title="Notes",
            url="https://x.test/doc1",
            text="\n\n".join("paragraph " + "y" * 300 for _ in range(4)),
            settings=settings(),
            http_client=client,
        )

    rows = (await db.execute(select(DocumentChunk))).scalars().all()
    assert stored == len(rows) > 1
    assert {row.dims for row in rows} == {4}
    assert {row.model for row in rows} == {"fake-model"}


@pytest.mark.asyncio
async def test_reindexing_replaces_rather_than_accumulates(db):
    """A document edited five times must not answer five different ways at
    once."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="the original text", settings=settings(), http_client=client,
        )
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="completely different text now", settings=settings(), http_client=client,
        )

    rows = (await db.execute(select(DocumentChunk))).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "completely different text now"


@pytest.mark.asyncio
async def test_an_unchanged_document_is_recognised_as_already_indexed(db):
    """What makes a crawl safe to run on a schedule: the second run embeds
    nothing."""
    embedder = FakeEmbedder()
    text = "some stable content"
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text=text, settings=settings(), http_client=client,
        )

    assert await semantic.is_indexed(
        db, source_key="s", external_id="d", text=text, model="fake-model"
    )
    assert not await semantic.is_indexed(
        db, source_key="s", external_id="d", text=text + " edited", model="fake-model"
    )


@pytest.mark.asyncio
async def test_a_document_indexed_by_another_model_does_not_count(db):
    """Changing the embedding model makes every stored vector stale even
    though the text is identical."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="content", settings=settings(), http_client=client,
        )

    assert not await semantic.is_indexed(
        db, source_key="s", external_id="d", text="content", model="a-different-model"
    )


# --- searching --------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_the_closest_document_first(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        for external_id, text in [("a", "aaaa aaaa"), ("b", "eeee eeee"), ("c", "zzzz zzzz")]:
            await semantic.index_document(
                db, source_key="s", external_id=external_id, title=external_id, url=None,
                text=text, settings=settings(), http_client=client,
            )
        found = await semantic.search(
            db, query="aaaa aaaa", settings=settings(), http_client=client, limit=5
        )

    assert found[0]["external_id"] == "a"


@pytest.mark.asyncio
async def test_a_long_document_takes_one_slot_not_forty(db):
    """Scored per document, taking its best passage. The same defect that had
    GitLab's `pnpm-lock.yaml` filling half a result list."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="long", title="Long", url=None,
            text="\n\n".join("paragraph " + "a" * 300 for _ in range(8)),
            settings=settings(), http_client=client,
        )
        found = await semantic.search(
            db, query="paragraph", settings=settings(), http_client=client, limit=10
        )

    assert len(found) == 1


@pytest.mark.asyncio
async def test_vectors_from_another_model_are_skipped_not_compared(db):
    """Two models' vectors are not on the same scale, and comparing them
    produces numbers that look perfectly reasonable."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="content", settings=settings(), http_client=client,
        )
        found = await semantic.search(
            db,
            query="content",
            settings=settings(embeddings_model="some-other-model"),
            http_client=client,
            limit=5,
        )

    assert found == []


@pytest.mark.asyncio
async def test_search_on_an_empty_index_returns_nothing_without_embedding(db):
    """No index means no question to ask - and asking anyway would bill an
    embedding call to answer with an empty list."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        found = await semantic.search(
            db, query="anything", settings=settings(), http_client=client, limit=5
        )

    assert found == []
    assert embedder.calls == []


@pytest.mark.asyncio
async def test_the_score_floor_drops_weak_matches(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="zzzz", settings=settings(), http_client=client,
        )
        found = await semantic.search(
            db,
            query="aaaa",
            settings=settings(semantic_min_score=0.999),
            http_client=client,
            limit=5,
        )

    assert found == []


# --- cosine -----------------------------------------------------------------


def test_cosine_is_one_for_identical_vectors():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_is_zero_against_a_zero_vector():
    """Rather than dividing by zero. An empty passage is not similar to
    anything, including itself."""
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_refuses_to_compare_different_lengths():
    """`zip` would silently compare a prefix, which is the failure least
    likely to be noticed."""
    with pytest.raises(ValueError):
        cosine([1.0, 2.0], [1.0, 2.0, 3.0])


# --- pages: what makes a picture attachable --------------------------------


def test_page_chunks_record_the_page_they_came_from():
    """A chunk is text; the answer to a spatial question is a picture. Without
    the page number the answer stage can only guess which page to attach."""
    pages = ["first page text", "second page text", "third page text"]

    numbered = semantic.chunk_pages(pages)

    assert [page for page, _ in numbered] == [0, 1, 2]


def test_a_passage_never_spans_a_page_boundary():
    """Each page is chunked on its own. A chunk stitched across pages 3 and 4
    could only ever name one of them, and would be wrong half the time."""
    pages = ["a" * 400, "b" * 400]

    numbered = semantic.chunk_pages(pages, size=1000, overlap=100)

    for page, text in numbered:
        assert set(text) == {"a"} if page == 0 else set(text) == {"b"}


def test_a_blank_page_still_consumes_its_number():
    """The index *is* the page number. Renumbering would attach the wrong
    picture to the right answer."""
    numbered = semantic.chunk_pages(["first", "", "third"])

    assert [page for page, _ in numbered] == [0, 2]


@pytest.mark.asyncio
async def test_indexing_pages_stores_the_page_on_each_chunk(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="google_drive", external_id="pdf1", title="Datasheet", url=None,
            text="page one\n\npage two", pages=["page one", "page two"],
            settings=settings(), http_client=client,
        )

    rows = (await db.execute(select(DocumentChunk).order_by(DocumentChunk.chunk_index))).scalars().all()
    assert [row.page_index for row in rows] == [0, 1]


@pytest.mark.asyncio
async def test_a_document_without_pages_stores_no_page(db):
    """Most of the corpus has none - a spreadsheet, a source file, a row."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="gitlab", external_id="f", title="main.go", url=None,
            text="some source code", settings=settings(), http_client=client,
        )

    rows = (await db.execute(select(DocumentChunk))).scalars().all()
    assert all(row.page_index is None for row in rows)


@pytest.mark.asyncio
async def test_search_reports_which_pages_matched(db):
    """This is what the answer stage renders instead of guessing."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="google_drive", external_id="pdf1", title="Datasheet", url=None,
            text="x", pages=["aaaa aaaa", "zzzz zzzz", "aaaa aaaa aaaa"],
            settings=settings(), http_client=client,
        )
        found = await semantic.search(
            db, query="aaaa", settings=settings(), http_client=client, limit=5
        )

    assert found
    # Both 'a' pages matched; the better one is named first.
    assert found[0]["matched_pages"][0] in (0, 2)
    assert set(found[0]["matched_pages"]) >= {0, 2}


@pytest.mark.asyncio
async def test_pages_below_the_floor_are_not_reported_as_matched(db):
    """Attaching a page whose text did not match is worse than attaching
    none - it spends the image budget on the wrong picture."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="google_drive", external_id="pdf1", title="D", url=None,
            text="x", pages=["aaaa", "zzzz"],
            settings=settings(), http_client=client,
        )
        found = await semantic.search(
            db, query="aaaa",
            settings=settings(semantic_min_score=0.999),
            http_client=client, limit=5,
        )

    assert found == []


@pytest.mark.asyncio
async def test_a_document_with_no_pages_reports_no_matched_pages(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="gitlab", external_id="f", title="t", url=None,
            text="aaaa", settings=settings(), http_client=client,
        )
        found = await semantic.search(
            db, query="aaaa", settings=settings(), http_client=client, limit=5
        )

    assert found[0]["matched_pages"] == []


# --- the index as a cache ----------------------------------------------------


@pytest.mark.asyncio
async def test_stored_text_returns_what_the_document_was_indexed_from(db):
    """The crawler already downloaded and parsed this document. Fetching it
    again to build a snippet is paying twice for the same bytes - measured, a
    Drive search spent 11 seconds doing exactly that against a semantic search
    of 0.66 over the same corpus."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="google_drive", external_id="doc1", title="t", url=None,
            text="alpha", pages=["alpha", "beta"], settings=settings(), http_client=client,
        )

    text = await semantic.stored_text(
        db, source_key="google_drive", external_id="doc1", model="fake-model"
    )

    assert text is not None
    assert "alpha" in text and "beta" in text


@pytest.mark.asyncio
async def test_stored_text_keeps_the_document_in_order(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="x", pages=["first", "second", "third"],
            settings=settings(), http_client=client,
        )

    text = await semantic.stored_text(db, source_key="s", external_id="d", model="fake-model")

    assert text.index("first") < text.index("second") < text.index("third")


@pytest.mark.asyncio
async def test_an_unindexed_document_is_a_miss_not_an_empty_string(db):
    """The caller fetches it as it always did. An empty string would be a
    snippet claiming the document has no content."""
    assert (
        await semantic.stored_text(db, source_key="s", external_id="never", model="fake-model")
        is None
    )


@pytest.mark.asyncio
async def test_text_indexed_by_another_model_is_not_served(db):
    """Same reason vectors from another model are never compared: the row
    belongs to an index this deployment is no longer using."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="content", settings=settings(), http_client=client,
        )

    assert await semantic.stored_text(db, source_key="s", external_id="d", model="other") is None


# --- grids: a table chunked like prose is a table destroyed -----------------


GRID = "\n".join(
    ["sheet: 'Fall Semester'!A1:AL1000", "R2: B=Тиждень  C=1  D=2", "R3: C=Вересень  M=Жовтень",
     "R4: B=Завдання  C=1  D=8  G=18", "R5: legend X=done"]
    + [f"R{n}: B=task {n}  G=X" for n in range(6, 80)]
)


def test_a_rendered_sheet_is_recognised_as_a_grid():
    assert semantic.is_grid(GRID)
    assert not semantic.is_grid("just some prose about a sheet")


def test_every_grid_chunk_carries_the_header_band():
    """The rows that give a cell its meaning are at the very top and are never
    adjacent to the data. Measured on the Gantt sheet: the band landed in
    chunk 0 and every data row in chunks 1 and 2, so `R31: B=3D-друк G=X` said
    only that something happened in column G - and "when did we finish the
    chassis design" became "the table does not give a date"."""
    chunks = semantic.chunk_grid(GRID, size=400, overlap=1)

    assert len(chunks) > 1
    for piece in chunks:
        assert piece.startswith("sheet: 'Fall Semester'")
        assert "R3: C=Вересень" in piece
        assert "R4: B=Завдання" in piece


def test_grid_chunks_carry_the_data_rows_between_them():
    chunks = semantic.chunk_grid(GRID, size=400, overlap=1)
    seen = " ".join(chunks)

    for n in (6, 40, 79):
        assert f"R{n}: B=task {n}" in seen


def test_a_sheet_that_fits_is_one_chunk():
    small = "sheet: X\nR2: B=a\nR3: B=b\nR4: B=c\nR5: B=d\nR6: B=data"

    assert len(semantic.chunk_grid(small, size=900)) == 1


def test_a_header_band_wider_than_the_budget_still_leaves_room_for_data():
    """Otherwise a wide sheet produces a chunk of header and nothing else."""
    wide = "sheet: X\n" + "\n".join(f"R{n}: " + "c" * 300 for n in range(2, 6))
    wide += "\n" + "\n".join(f"R{n}: B=row{n}" for n in range(6, 20))

    chunks = semantic.chunk_grid(wide, size=400, overlap=1)

    assert any("B=row" in piece for piece in chunks)


@pytest.mark.asyncio
async def test_indexing_a_sheet_uses_the_grid_chunker(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="google_drive", external_id="sheet1", title="Gantt", url=None,
            text=GRID, settings=settings(), http_client=client,
        )

    rows = (await db.execute(select(DocumentChunk))).scalars().all()
    assert len(rows) > 1
    assert all(row.text.startswith("sheet: 'Fall Semester'") for row in rows)


@pytest.mark.asyncio
async def test_a_sheet_is_recorded_as_a_grid_by_whoever_cut_it(db):
    """Not by the caller. The crawler learns a hit's `snippet_format` from the
    listing, where a spreadsheet has not been rendered yet and reports plain
    text - and that flag then told the answer prompt to treat a grid as prose,
    which excerpts a window over the middle and cuts off the header band the
    grid chunker had just repeated into every chunk."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="google_drive", external_id="sheet1", title="Gantt", url=None,
            text=GRID, settings=settings(), http_client=client,
            meta={"snippet_format": "text"},  # what the listing wrongly said
        )

    rows = (await db.execute(select(DocumentChunk))).scalars().all()
    assert {row.meta["snippet_format"] for row in rows} == {"grid"}


@pytest.mark.asyncio
async def test_prose_is_not_recorded_as_a_grid(db):
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="s", external_id="d", title="t", url=None,
            text="ordinary prose about a spreadsheet",
            settings=settings(), http_client=client,
        )

    rows = (await db.execute(select(DocumentChunk))).scalars().all()
    assert {row.meta["snippet_format"] for row in rows} == {"text"}


@pytest.mark.asyncio
async def test_search_reports_the_recorded_format(db):
    """This is what the connector turns into `snippet_format` on the hit, and
    what stops a grid being excerpted."""
    embedder = FakeEmbedder()
    async with embedder.client() as client:
        await semantic.index_document(
            db, source_key="google_drive", external_id="sheet1", title="Gantt", url=None,
            text=GRID, settings=settings(), http_client=client,
        )
        found = await semantic.search(
            db, query="task", settings=settings(), http_client=client, limit=5
        )

    assert found[0]["snippet_format"] == "grid"


# --- workbooks: a tab is not a section of another tab ------------------------


FALL = "\n".join(
    ["sheet: 'Fall Semester'!A1:AL1000", "R2: B=Тиждень  C=1", "R3: C=Вересень  M=Жовтень",
     "R4: B=Завдання  C=1  D=8", "R5: legend"]
    + [f"R{n}: B=fall task {n}  C=X" for n in range(6, 40)]
)
SPRING = "\n".join(
    ["sheet: 'Spring Semester'!A1:AL1000", "R2: B=Тиждень  C=1", "R3: C=Березень  M=Квітень",
     "R4: B=Завдання  C=3  D=10", "R5: legend"]
    + [f"R{n}: B=spring task {n}  C=X" for n in range(6, 40)]
)
WORKBOOK = FALL + "\n" + SPRING


def test_a_workbook_splits_into_one_section_per_tab():
    assert len(semantic.split_tabs(WORKBOOK)) == 2


def test_a_single_tab_is_one_section():
    assert len(semantic.split_tabs(FALL)) == 1


def test_no_chunk_carries_two_tabs_headers():
    """`render_tabs` concatenates every tab. Treating that as one grid took the
    FIRST tab's header band and prefixed it to every other tab's rows."""
    chunks = semantic.chunk_grid(WORKBOOK, size=400, overlap=1)

    assert all(piece.count("sheet: ") == 1 for piece in chunks)


def test_a_tabs_rows_are_never_filed_under_another_tabs_header():
    """Measured: five chunks of Spring Semester data carried Fall Semester's
    months and days, so a question about the second tab was answered from the
    first - confidently, and about the wrong half of the year."""
    chunks = semantic.chunk_grid(WORKBOOK, size=400, overlap=1)

    for piece in chunks:
        if "spring task" in piece:
            assert "Spring Semester" in piece
            assert "Березень" in piece
            assert "Вересень" not in piece
        if "fall task" in piece:
            assert "Fall Semester" in piece
            assert "Вересень" in piece


def test_every_tab_reaches_the_index():
    chunks = semantic.chunk_grid(WORKBOOK, size=400, overlap=1)
    seen = " ".join(chunks)

    assert "fall task 39" in seen
    assert "spring task 39" in seen
