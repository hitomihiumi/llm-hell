"""Merging results from sources whose scores mean different things.

**Reciprocal rank fusion, deliberately ignoring each source's own relevance
score.** GitLab returns no score at all, postgres-mcp returns whatever the
generated `ORDER BY` produced, and Drive returns Google's own opaque
ordering. These are not on one scale and cannot be made comparable by
normalising them - a source that happens to emit larger numbers would simply
win. The only signal that means the same thing everywhere is *position
within that source's own results*, which is exactly what RRF consumes:

    score = sum over every ranked list of  source_weight / (k + rank)

`k = 60` is the constant from the original RRF paper and is large on
purpose: it flattens the curve so rank 1 does not swamp rank 3, which
matters when the sources disagree about what "relevant" means.

**The sum is across phrasings, and that is the point.** The planner runs
several queries per question, so every source produces several ranked lists.
Collapsing them by keeping each document's best rank - which is what this
used to do - throws away the strongest relevance signal available for free:
a document found by every phrasing is far more likely to be the answer than
one found by a single lucky phrasing. Measured, with best-rank-wins: asked
what the auth-service README said, `package.json` came first, because one
phrasing happened to rank it top. Summing instead, four phrasings agreeing at
rank 1 score 4/61, which beats a one-off rank 0 at 1/60 - agreement wins.

This is a heuristic and is meant to be read as one. It is not tuned against
any judged relevance set, and pretending otherwise would be worse than
saying so.
"""

from datetime import UTC, datetime

from app.schemas.search import SearchHit

# Beyond this, a hit gets no recency credit at all. Two years is long enough
# that a stale runbook stops competing with a current one.
RECENCY_HORIZON_DAYS = 730

# Maximum multiplier applied to a brand-new document, and it has to be this
# small for a specific arithmetic reason.
#
# The RRF curve at k=60 is very flat: adjacent ranks differ by a factor of
# only (60+r+1)/(60+r), about 1.6% at the top of the list. A boost of b can
# therefore leapfrog roughly b*k positions - at b=0.15 that is NINE places,
# which would make recency the dominant signal and the rank order almost
# decorative. Keeping b at about 1/k confines it to a single position, which
# is what "tie-breaker" actually means.
#
# Raise `k` if you want recency to carry more weight; do not raise this
# without recomputing b*k.
RECENCY_MAX_BOOST = 0.016


def recency_factor(timestamp: datetime | None, *, now: datetime | None = None) -> float:
    """1.0 for undated or old hits, up to 1 + RECENCY_MAX_BOOST for fresh ones.

    Undated hits score 1.0 rather than 0 - most GitLab code hits carry no
    timestamp, and penalising them for it would push an entire source down
    the list for a reason unrelated to relevance.
    """
    if timestamp is None:
        return 1.0
    now = now or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    age_days = (now - timestamp).total_seconds() / 86400
    if age_days <= 0:
        return 1.0 + RECENCY_MAX_BOOST
    if age_days >= RECENCY_HORIZON_DAYS:
        return 1.0
    return 1.0 + RECENCY_MAX_BOOST * (1.0 - age_days / RECENCY_HORIZON_DAYS)


def fuse(
    attempts_by_source: dict[str, list[list[SearchHit]]],
    *,
    weights: dict[str, float] | None = None,
    k: int = 60,
    total_limit: int = 40,
    per_source_cap: int | None = None,
    now: datetime | None = None,
) -> list[SearchHit]:
    """Interleave every source's ranked lists into one.

    `attempts_by_source` maps a source to the ranked lists it produced - one
    per planned phrasing, so usually several. A source that ran one query has
    a list of one list.

    `per_source_cap` stops one chatty source from filling the whole answer
    prompt. Without it, a KB query matching forty rows leaves no room for the
    two GitLab hits that might have been the actual answer. It is applied to
    the *result*, not to each input list: capping the inputs would silently
    change what the sum is over.

    Mutates each hit's `score` and `rank_in_source` - the latter to the best
    position any phrasing gave it - so the fused ordering stays explainable
    after the fact.
    """
    weights = weights or {}

    # Accumulated across every phrasing, keyed on hit id so the same document
    # found by two queries is one entry rather than two.
    totals: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    chosen: dict[str, SearchHit] = {}

    for source_key, attempts in attempts_by_source.items():
        weight = weights.get(source_key, 1.0)
        for attempt in attempts:
            for rank, hit in enumerate(attempt):
                totals[hit.id] = totals.get(hit.id, 0.0) + weight / (k + rank)
                if hit.id not in best_rank or rank < best_rank[hit.id]:
                    best_rank[hit.id] = rank
                    chosen[hit.id] = hit

    for hit_id, hit in chosen.items():
        hit.score = totals[hit_id]
        hit.rank_in_source = best_rank[hit_id]

    # Recency is a tie-break, and deliberately sits *after* the source key so
    # that it can only ever order hits from the same source.
    #
    # As a multiplier on the score it was not a tie-break at all but a
    # structural bias between sources: undated hits get 1.0 and dated ones up
    # to 1.016, GitLab code hits carry no timestamp and Drive documents always
    # do, so Drive outranked GitLab at equal rank on the strength of having
    # dates rather than of being relevant. Measured: Drive's `.env` came top
    # for "auth-service README" that way.
    ordered = sorted(
        chosen.values(),
        key=lambda hit: (
            -hit.score,
            hit.source,
            -recency_factor(hit.timestamp, now=now),
            hit.rank_in_source,
            hit.id,
        ),
    )

    if per_source_cap is not None:
        kept: list[SearchHit] = []
        seen: dict[str, int] = {}
        for hit in ordered:
            if seen.get(hit.source, 0) >= per_source_cap:
                continue
            seen[hit.source] = seen.get(hit.source, 0) + 1
            kept.append(hit)
        ordered = kept

    return ordered[:total_limit]
