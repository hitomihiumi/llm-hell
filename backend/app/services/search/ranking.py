"""Merging results from sources whose scores mean different things.

**Reciprocal rank fusion, deliberately ignoring each source's own relevance
score.** GitLab returns no score at all, postgres-mcp returns whatever the
generated `ORDER BY` produced, and Drive returns Google's own opaque
ordering. These are not on one scale and cannot be made comparable by
normalising them - a source that happens to emit larger numbers would simply
win. The only signal that means the same thing everywhere is *position
within that source's own results*, which is exactly what RRF consumes:

    score = source_weight / (k + rank_within_source)

`k = 60` is the constant from the original RRF paper and is large on
purpose: it flattens the curve so rank 1 does not swamp rank 3, which
matters when the sources disagree about what "relevant" means.

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
    hits_by_source: dict[str, list[SearchHit]],
    *,
    weights: dict[str, float] | None = None,
    k: int = 60,
    total_limit: int = 40,
    per_source_cap: int | None = None,
    now: datetime | None = None,
) -> list[SearchHit]:
    """Interleave every source's hits into one ranked list.

    `per_source_cap` stops one chatty source from filling the whole answer
    prompt. Without it, a KB query matching forty rows leaves no room for the
    two GitLab hits that might have been the actual answer.

    Mutates each hit's `score` and reassigns `rank_in_source` to the position
    the source actually returned it in, so the fused ordering stays
    explainable after the fact.
    """
    weights = weights or {}
    scored: list[SearchHit] = []

    for source_key, hits in hits_by_source.items():
        weight = weights.get(source_key, 1.0)
        capped = hits[:per_source_cap] if per_source_cap else hits
        for rank, hit in enumerate(capped):
            hit.rank_in_source = rank
            hit.score = (weight / (k + rank)) * recency_factor(hit.timestamp, now=now)
            scored.append(hit)

    # Sort by score, then by source key and rank so the order is total and
    # a tie does not shuffle between identical requests.
    scored.sort(key=lambda hit: (-hit.score, hit.source, hit.rank_in_source))
    return scored[:total_limit]
