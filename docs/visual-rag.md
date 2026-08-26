# Visual RAG — plan

A question whose answer lives in a diagram has to be answered from the
diagram. Not from a transcription of it, not from the text layer beside it —
from the pixels, in the same prompt as everything else.

This is the plan for getting there. It is written after measuring what is
already in place, so most of it is smaller than it sounds.

---

## Where this starts

**The answer model already accepts images, and the machinery was switched
off.** `answer.build_prompt` attaches page pictures as `image_url` parts
beside the text results, and `api/search._page_images` renders them. Its own
docstring states the intent exactly:

> One model, one pass: the pages go into the answer prompt beside the text
> results rather than being described first by a second model. Nothing sits
> between the picture and the answer, so nothing a transcriber failed to
> mention can be lost.

It produced nothing because it was gated behind a *second* model id,
`VISION_MODEL_ID`, which was empty — and `select_vision_endpoint` returned
`None` on an empty value before looking at any endpoint. One unset variable
disabled the whole visual path.

That second id is now gone. **The model that writes the answer is the model
that sees the page**, with no duplicate setting and no duplicate endpoint row;
a separate vision model could only ever supply a description, which is the
thing this is for avoiding. The change turned a refusal into an answer:

```
before   85 chars, 0 citations   "the provided documents contain no information…"
after    "USB-порт на платі MATEKSYS F722-HD розташований ліворуч від MCU
          (STM32F722RET6) [1] (див. зображення сторінки 1 та 2 результату [1])."
```

Note what the model says about its own sources: it is reading the *page
images*, and says so.

**The retrieval side already exists too**: `document_chunks`, `crawl.py`,
`embeddings.py`, the `semantic` connector, and a TEI container serving
`multilingual-e5-small`. 92 documents / 997 chunks indexed. The connector was
switched off after measurement, for reasons in `backend/evals/README.md`.

So this plan is not "build RAG". It is "make the retriever and the pictures
meet".

---

## What is actually missing

### 1. A chunk knows its document, not its page

`document_chunks` stores `chunk_index` — a position in the concatenated text —
and nothing about which *page* that text came from. So when a chunk matches,
there is no way to ask for the page it was on.

### 2. Pages are chosen by how they look, not by what was asked

`page_images` ranks pages with `select_visual_pages(page_stats(...))` — the
most *visual* pages, decided without seeing the question. For a four-page
datasheet that is fine. For a forty-page one it is a lottery: the answer is on
the page that matched, and the prompt gets the pages with the most ink.

### 3. The image path is hard-wired to one source

`_page_images` returns early unless `hit.source == SOURCE_GOOGLE_DRIVE`. Today
only Drive renders pages, so the behaviour is right and the *shape* is wrong —
a second source that can render pages would be silently skipped.

### 4. Two visual paths exist and only one is honest

There is also `document_pages`: a vision model transcribes a page to text,
which is then indexed and searched. That is useful for *finding* a document —
a term printed only inside a diagram becomes searchable — and it is exactly
the lossy description this plan is trying not to answer from. The two need
distinct roles, written down, or they will drift into doing each other's job.

### 5. Retrieval alone should not decide

Measured on 27 cases: semantic-only retrieval scored MRR 0.636 against 0.803
for lexical, and the queries it lost were the ones naming a thing — `t motor
u7`, a file named by its hash. Embeddings carry meaning and drop identifiers.

---

## The plan, and what is done

### Stage 1 — chunks remember their page ✅

`DocumentChunk.page_index` (nullable — most of the corpus has no pages),
migration `0006`. `pdf.page_texts` extracts the text layer page by page;
`page_stats` already did this and threw the text away. `semantic.chunk_pages`
chunks **each page on its own**, so a passage never spans a page boundary — a
chunk stitched across pages 3 and 4 could only ever name one of them and would
be wrong half the time. A blank page still consumes its number, because the
index *is* the page number.

Live: 62 of 264 Drive chunks carry a page. The rest are spreadsheets and files
with no pages, which is correct.

### Stage 2 — the matched page is the attached page ✅

`semantic.search` reports `matched_pages` — every page of a document whose
chunk cleared the score floor, best first. `page_images(hit_id, pages=[...])`
renders exactly those; `select_visual_pages` stays as the fallback for a hit
whose retriever knows nothing about pages, which is every lexical hit.

**The merge was the part that nearly did not work.** Fusion scores per
document, and when the same file is found both lexically and semantically the
lexical hit usually wins on rank — so the merged hit came out with no pages at
all. Measured live, that turned a correct answer into a wrong one: asked
whether the F722 board has a gyroscope, the model was shown whichever pages
looked most visual and replied that it does not. `fuse` now merges
`matched_pages` across every retriever that found the document, and keeps the
answer correct.

### Stage 3 — generalise the source gate ✅

`_page_images` asked `hit.source == SOURCE_GOOGLE_DRIVE`; it now asks whether
the connector exposes `page_images` at all. Behaviour is identical today —
only Drive renders pages — and the next source that can will be noticed
without editing this function.

### Stage 4 — spend the image budget deliberately ✅

The budget is spent on **pages**, not on hits. It used to be handed out a
whole hit at a time: the first two hits that could render anything took
everything, however little their pages had to do with the question, and a
third hit whose retriever knew exactly which page answered it got nothing.
Now each hit offers its best matched page, then its second, round by round
until the budget is gone — so a highly ranked hit gets more pages than a low
one without ever starving it, and a hit that knows *which* page matched is
served before one that can only offer "the page with the most ink".

The allocation lives in `services/search/images.py`, apart from the routes,
because both `/api/search` and the coding provider need it and had been
carrying their own copy — a subtle algorithm duplicated twice is a bug
waiting for one copy to be fixed.

Two of its bugs are pinned by tests, both mine and both found by writing them:
a `break` that fired before any hit without matched pages was ever offered
anything, and the whole-hit allocation this replaced.

### Stage 5 — hybrid retrieval ✅

`semantic` is enabled **alongside** the lexical sources at weight 0.6 (the
value hybrid measured best at earlier: MRR 0.806 against 0.794 at weight 1.0).
Not instead of them: measured, semantic-only lost exactly the queries naming a
thing — `t motor u7`, a file named by its hash.

### Stage 6 — which path is which ✅

`document_pages` (transcription) exists to make a picture **findable**: a term
printed only inside a diagram is invisible to Drive's text-layer index.
The page image exists to be what an answer is **written from**. A
transcription must never be the thing an answer is written from when the
original page can be attached instead — that is the whole point, and it is
why there is no separate vision model.

## The index also made search faster

Not planned, but it fell out of having the corpus indexed, and it is the
largest latency change in the project.

Measured per source, `answer: false`:

| source | before | after |
| --- | --- | --- |
| google_drive | 11.2s / 13.3s | **2.7s / 4.8s** |
| postgres_kb | 6.3s | 6.2s |
| semantic | 0.7s | 0.8s |
| **wall clock** | **12.0s / 14.5s** | **8.7s / 7.6s** |

A search waits for its slowest source, and that source was Drive by an order
of magnitude. Drive's own search returns metadata with **empty snippets**, so
`_enrich` then downloads and parses the top documents to have something to
quote — and the semantic crawler had already downloaded and parsed exactly
those documents to chunk them.

`semantic.stored_text` serves that text from the index, so an indexed document
is not fetched twice. A miss is normal and costs nothing: the document is
simply not indexed yet and is fetched as before.

The remaining floor is `postgres_kb` at ~6s, which is a text2sql model call
followed by a query, and which returned zero hits on both of these questions.
That is the next thing worth looking at, and it is not a RAG problem.

## Two things the index was silently missing

Found by running with every source but `semantic` switched off, which is the
only configuration that shows what the index does and does not hold.

### An image file has no text, so it was never indexed at all

The crawler reads a document, finds nothing, and skips it. A drawing has no
text layer, so `T Motor U7 V2.0 Power Type UAV KV490.png` — the file with the
motor's dimensions on it — was not among the thirteen Drive documents in the
index. The question could not be answered because nothing about that file was
searchable.

`_describe_visually` now describes such a file at crawl time and indexes the
description, flagged `described: true` in the chunk's metadata. **That
description exists to make the file findable and is never what an answer is
written from** — `page_images` still attaches the drawing itself, and the
model reads it. An index entry is not evidence.

```
before   "У наданих документах не зазначено фізичні розміри мотора T-Motor"
after    "⌀60,7 · довжина 45 · вал ⌀10 · кріплення ⌀24, ⌀20, ⌀17, ⌀12 · 8-M3 та 4-M3"
```

### A grid chunked like prose is a grid destroyed

The rows that give a cell its meaning sit at the very top of a sheet and are
never adjacent to the data. Chunking by character count put the header band in
chunk 0 and every data row in chunks 1 and 2, so a retrieved chunk read
`R31: B=3D-друк деталей  G=X` — something happened in column G, and the row
saying G is the 18th of September was in a different chunk entirely.

`chunk_grid` repeats the header band into every chunk. It costs a few hundred
characters per chunk and is the difference between a row that can be read and
one that cannot. `snippet_format` is carried through the index too, so a
semantically-retrieved sheet still reaches the answer prompt as a grid — the
prompt's rules for merged headers and its larger character budget both hang
off that field — and a grid is passed whole rather than excerpted, because a
window over the middle would cut the band straight back off.

```
before   "конкретна дата або термін завершення в цій таблиці відсутні"
after    "Проєктування шасі було завершено до 3 вересня 2025 року [6]"
```

### What this costs

Retrieval from the index alone is **1.8s**, against 8–14s for the federated
lexical search. The answer then takes **12.9s**, because the model is reading
page images — that is the price of a correct answer to a question about a
drawing, and `answer_image_hits` and `vision_max_pages` are the dials if it is
too high a price for a given deployment.

## The index refreshes itself

`manage.py index-corpus` works, but it is a command somebody has to remember.
**An index nobody refreshes is worse than no index**: it answers confidently
from a corpus that has moved on, and there is nothing in a stale answer that
says so. A document added this morning is simply invisible, and the search
reports no error while being wrong.

So the crawl runs on a timer inside the API process, started from the
lifespan after the MCP registry (the crawl searches through the same
connectors) and cancelled before it on shutdown, so nothing races a pass that
is halfway through writing rows.

```
SEMANTIC_INDEX_INTERVAL_MINUTES=30   # 0 turns it off
```

It does nothing at all unless `SEMANTIC_ENABLED` is on: a deployment not using
the index should not pay for a crawl of every source it has.

**The timer is cheap because the crawl is incremental.** Every document is
fingerprinted by length and skipped when that version is already indexed. The
first automatic pass on a live stack:

```
semantic index: refreshing every 30 minutes
semantic index: 1 indexed (1 chunks), 95 unchanged, 0 failed
```

95 documents cost one listing call per source and no embeddings at all.

Three things this deliberately does *not* do, each for a reason:

- **It does not crawl on startup.** The first pass waits a minute, so a
  container coming up is not answering its first requests while also reading
  every source.
- **It does not stop on a failure.** A source down at 09:00 is usually up at
  09:30, and a crawl that died on the first refused connection would freeze
  the index at whatever it held when the network last hiccuped.
- **It takes no lock.** The compose file runs uvicorn without `--workers` on
  purpose - the Google MCP server can be a child of this process, and N
  workers would mean N subprocesses writing one OAuth token file - so there is
  exactly one of this loop. Scale the API out and this needs a lock before it
  needs anything else, or every replica crawls the same corpus at once.

Note that the crawl fills the index from every crawlable source regardless of
whether that source is *enabled for searching*. That is the right way round
for a semantic-only deployment: the lexical connectors can be switched off
while their documents still reach the index.

## How each stage is checked

`backend/evals/run_answers.py` scores the answer stage, not retrieval:

```
retrieved the answer   did the right document come back
cited the answer       did the answer use it
IGNORED what it found  retrieval succeeded, the answer refused ← the metric
```

Before enabling vision, 6 of 27 cases were `ignored` — the document was there
and the answer did not use it, three of them refusals under 240 characters.
That number is the target, and it is the only one that moves when the visual
path improves; every retrieval metric already scores those cases as successes.

Run it before and after each stage. `run.py` and `validate.py` cover
retrieval and label staleness respectively.

### One regression this caught by hand

With images finally attached, the F722 question came back correct and with
**zero citations**. The model had written `[1 (page image 2)]` — copying the
label the picture was announced with, `[N] page image M`, which is the
citation syntax with something added inside the brackets. `extract_citations`
matches `[n]` and nothing else, so a correct answer scored as a refusal.

The label is now `Result N, page image M` — no brackets to copy — and a test
asserts a picture label never starts with one.

---

## Order, and why

Stages 1 and 2 are the substance — everything else is tidying or tuning. 3 and
4 are cheap and can ride along. 5 should come last, because enabling a
retriever before the pictures follow it would measure the wrong thing: a
document found by meaning and answered from its text layer is exactly the
failure this plan exists to remove.
