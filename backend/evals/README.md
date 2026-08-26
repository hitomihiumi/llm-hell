# Search evaluation

A small judged set, so a change to retrieval can be shown to help rather than
argued about.

It exists because guessing kept being wrong. Telling the agent's model to stop
truncating its sentences was measured after the fact and had made things
slightly worse. Fusing results across query phrasings was expected to fix
`package.json` outranking the README it was asked about, and did not. Both
sounded right. Neither was checked first.

## Where the labels come from

Nobody hand-labelled anything. Every answered search already recorded which
results the answer cited, and a citation is an implicit relevance judgement:
the model read that document and used it.

That signal is noisy — asked what the auth-service README says, one run cited
the repository (correct) along with `.env` and a weekly progress report
(clearly not). So `build_cases.py` groups identical questions, gives each cited
document one vote per run, and proposes only those carrying at least half the
vote **and at least two runs**. Half of two is one, so a bare share let
everything through on a question asked twice — exactly the sample size where
the vote is needed.

The draft was then read and corrected by hand. `cases.json` records why:
`label.runs` is how many runs a label rests on, `label.why` names any override.
Two corrections are worth knowing about, because they are the point of the
exercise:

- **"Що таке auth-service"** dropped `package.json`. The mined labels included
  it, but a `package.json` is not what "what is auth-service" means — it is the
  junk hit this set exists to catch, and labelling it correct would have taught
  the score to reward the bug.
- **"Занйди на gitlab репозиторій…"** had every mined label wrong: the search
  failed outright and cited four unrelated runbooks. The expectation was
  supplied from the three other cases that establish the same document.

`excluded` in the file records what was thrown out and why — mostly questions
that only mean something as a follow-up ("is there an imu here", "перескажи їх
зміст"), which cannot be scored without conversation history the runner does
not supply.

## Running it

The stack has to be up: this drives the real Drive, the real GitLab and the
real planner.

```bash
python backend/evals/run.py --label "what you changed" --out report.json
```

`answer: false`, so no model writes prose and no synthesis is billed. About six
minutes for 25 cases, most of it waiting on the sources.

Cases whose documents all live in a **disabled** source are skipped rather than
scored as misses — otherwise the number is a fact about the configuration
rather than about the search, and switching a source back on would look like a
retrieval win.

## What the numbers mean

| | |
| --- | --- |
| `MRR` | 1.0 if the first expected document was the top hit, 0.5 if second. The number that moves when *ranking* improves. |
| `recall@k` | did any expected document reach the top k at all — a *retrieval* failure rather than a ranking one |
| `noise` | share of returned hits no case expected |
| `found nothing` | cases where nothing expected came back, at any rank |

## Baselines

Two of them, because there are two things to get right and only one of them
was ever measured.

### Retrieval — `run.py`, `baseline.json`

All four sources enabled, the two narrow ones weighted 0.6:

```
cases            27
MRR              0.803
recall@1         0.704
recall@3         0.852
recall@10        1.000
found nothing    0
all expected     27/27
```

**Retrieval is saturated on this set.** recall@10 is 1.0 and every expected
document is found for every case. Nothing further can be measured here without
harder cases - the database holds 262 searches and only 48 distinct queries
were mined.

### Answers — `run_answers.py`, `baseline_answers.json`

```
cases                 27
retrieved the answer  27
cited the answer      21
IGNORED what it found  6   (rate 0.222)
no citations at all    3
hallucinated cites     0
```

**This is where the failures now live.** In 6 of 27 cases the document that
answers the question was retrieved and the answer cited none of them. Three
produced no citations at all, and their lengths give them away:

```
 85 chars, 0 cites   З якого боку від MCU розташований USB-порт на платі F722?
107 chars, 0 cites   in the F722-HD controller on what side is usb port relative to mcu?
237 chars, 0 cites   пошукай дату у діограмі гранта
```

An 85-character answer with no citations is a refusal. The F722 datasheet was
retrieved at rank 1; the model replied that the documents contain nothing about
it. Every retrieval metric in this directory scores that case as a success,
which is precisely why this second runner exists.

## Validate before believing a number

The first baseline read MRR 0.726, recall@10 0.87, three cases finding nothing.
Two of those three were chasing documents that **had been deleted from Drive**
since the labels were mined — `.env`, `TEST`, and the `Copy of Week N` variants
of reports that still exist under their real names. Nothing said so. They
scored as retrieval failures, and two rounds of tuning went into fixing a
search that was working correctly.

`validate.py` now checks every expected Drive document still exists before a
run is trusted. With the stale labels removed and the `Copy of` duplicates
retargeted onto the real files, recall@10 went from 0.87 to 0.95 and "found
nothing" from 3 to 1 — the search had been that much better than the number
said all along.

## What has been measured against it

Both of these sounded right. Both are recorded here so nobody spends the
afternoon again.

Measured against the *old* baseline (0.726 / 0.696 / 0.839), before the stale
labels were found. Both are still valid as comparisons — the same labels were
used on both sides — and both are still negative.

Three of the four were negative. The one that worked was found by reading a
diagnostic dump, not by reasoning about the ranking.

| change | MRR | recall@1 | recall@10 | verdict |
| --- | --- | --- | --- | --- |
| *old baseline, stale labels* | 0.726 | 0.696 | 0.870 | |
| Drive: match terms against `name` too | 0.718 | 0.652 | 0.870 | reverted |
| fusion: drop hits below 0.35 × top score | 0.705 | 0.652 | 0.870 | reverted |
| gate on term overlap | — | — | — | rejected before building |
| *baseline, labels validated* | 0.728 | 0.619 | 0.952 | |
| **score per document, not per line** | **0.849** | **0.762** | **1.000** | **kept** |
| semantic index as a fifth source | 0.667 | 0.571 | 0.952 | reverted |
| semantic hits carrying the document's own identity (hybrid) | 0.806 | 0.714 | 1.000 | reverted |
| semantic index as the *only* source | 0.636 | 0.522 | 0.826 | reverted |
| **enable postgres_kb + gmail** (27 cases) | 0.745 | 0.630 | 0.963 | kept |
| **…weighted 0.6** (27 cases) | **0.803** | **0.704** | **1.000** | **kept** |

The semantic rows are not directly comparable to the rest - the RAG-only run
scored 23 cases rather than 21, because the index made `postgres_kb` documents
reachable with its connector switched off. What the three of them together do
show is consistent: embeddings retrieved *meaning* well and *identifiers*
badly, and the queries they lost were exactly the ones naming a thing -
`t motor u7`, a file named by its hash. The code was removed; the measurements
are kept here so the idea is not re-tried blind.

**Matching the file name** was aimed at two cases that find nothing — a Drive
file called `.env` and one called `TEST`. The theory was that `fullText
contains` does not really cover the name, as its own documentation claims. It
fixed neither case and matched more files on common words, so noise went up.
The two misses are still unexplained.

**The score floor** was aimed at the 84% noise directly. It removed almost none
of it (0.8pp) and cost three cases their complete set of expected documents.
The reason is structural and worth keeping in mind: RRF scores a hit by its
rank *within its own source*, so GitLab's best irrelevant file scores exactly
what Drive's best relevant one does. **The noise is not in the tail — it is
interleaved at the top**, and no cutoff on a position-derived score can
separate them.

**A term-overlap gate** — "drop any hit sharing no distinctive word with the
question" — was measured *before* being built, which is the only reason it cost
ten minutes instead of an afternoon. Over 146 returned hits, 25 expected:
gating on the title would cut 93% of the noise and lose **64% of the expected
documents**; gating on title-plus-snippet would cut only 28% of noise. Neither
is a trade worth making. The one useful thing in that data is that title
overlap averages 0.60 on expected hits against 0.07 on noise — a strong
*positive* signal, and a useless negative one. If it is used at all, it should
boost, not filter.

## What the diagnosis actually points at

Running the three failing cases with per-source detail showed the same thing
each time: GitLab returns 10–26 hits per query, essentially all of them files
from one unrelated UI component library, for questions about 3D printing and
`.env` files. That matches its citation rate in production — 659 hits
returned, 11 ever cited, 1.7%.

The one remaining real miss points somewhere else entirely. Two phrasings of
the same question:

```
Які розміри t motor u7?      -> nothing found,  10 hits returned
які розміри у t motor u7?    -> rank 1,         20 hits returned
```

`search_terms` returns the same four terms for both, differing only in the
capitalisation of `Які`. So the divergence is in the **planner**: the same
question, trivially reworded, gets a different set of phrasings, and one of
them steers the search away from a document the other finds first. That is
consistent with the planner being corpus-blind — it invents plausible English
like `auth-service gitlab` with no idea what the corpus contains.

That hypothesis was wrong too, and cheaply: re-running all three phrasings
printed the *same* plan for each and returned the expected document at rank 1
every time. The eval failure had not been reproducible.

What the dump did show was GitLab returning ten hits that were all the same
file — `pnpm-lock.yaml`, matched on ten different lines. A code search returns
one hit per matching line, each with its own `id`, and fusion was scoring each
as a separate document. At a per-source cap of ten, one lockfile took half the
result list.

**Scoring per document fixed it.** Keyed on `external_id`, taking each
document's best position within a phrasing, so a file matching ten times is one
document at its best rank rather than ten competing entries. MRR went 0.728 to
0.849 and recall@10 to 1.0 — every case now finds what it is looking for.

Note that `noise` went slightly *up*, 0.795 to 0.821. That is the label
sparsity being visible: with duplicates no longer occupying slots, more
distinct documents come back, and a distinct document nobody labelled counts as
noise. It is a reason to trust MRR and recall over `noise`, not a regression.

### Still open

- **GitLab rate-limits itself.** `429 Too Many Requests` from the GitLab API,
  not from the MCP server, appears when four phrasings fan out across several
  projects. When it fires the source returns nothing at all.
- **GitLab precision is still 1.7%** in production — 659 hits returned, 11 ever
  cited. Deduplication removed the worst of the flood; it did not make the
  remaining hits relevant.

## Caveats, plainly

- **25 cases is small.** A change moving MRR by 0.02 is noise, not progress.
- **The labels come from this system's own answers.** A document the search
  never surfaced could not have been cited, so it cannot appear as expected —
  the set can measure ranking well and systematically *understates* what is
  missing entirely. It cannot find a document the corpus has and the search has
  never once returned.
- **Titles are the key**, since a hit id is unique to one response. Renaming a
  Drive file silently breaks a case.
- Questions are heavily weighted toward Drive, because the corpus is.
