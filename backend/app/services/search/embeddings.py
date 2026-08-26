"""Turning text into vectors, and comparing them.

The embedding server is Text Embeddings Inference, in its own container. It
speaks the OpenAI `/v1/embeddings` shape, so nothing here is specific to it
beyond the model name - swapping in any compatible endpoint is a URL change.

**The model is multilingual on purpose.** The questions this system is asked
are Ukrainian; the documents are mostly English. A monolingual embedder would
need the query planner to bridge that gap exactly as it does today, which
would leave the whole exercise measuring the planner again.

**e5 models want prefixes.** `intfloat/multilingual-e5-*` is trained with
`query:` on the question side and `passage:` on the document side, and
omitting them costs real accuracy - the two sides land in slightly different
parts of the space. The prefixes are applied here rather than by callers, so
there is one place that knows and no way to embed a query as a passage by
accident.
"""

import logging
import math

import httpx

from app.core.config import Settings

logger = logging.getLogger("llmhell.embeddings")

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

# One request per batch of this many passages. TEI batches internally; this
# only bounds the size of a single HTTP body, which a whole corpus would
# otherwise blow past.
BATCH = 32


class EmbeddingError(RuntimeError):
    """The embedder could not be reached or did not answer usefully."""


async def embed(
    texts: list[str],
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    kind: str = "passage",
) -> list[list[float]]:
    """Vectors for these texts, in the same order.

    Raises rather than returning empty. A search that silently produced no
    vectors would look exactly like a corpus with nothing in it, and the whole
    point of this index is to be able to tell those apart.
    """
    if not texts:
        return []
    prefix = QUERY_PREFIX if kind == "query" else PASSAGE_PREFIX
    out: list[list[float]] = []

    for start in range(0, len(texts), BATCH):
        batch = [prefix + text for text in texts[start : start + BATCH]]
        try:
            response = await http_client.post(
                f"{settings.embeddings_url.rstrip('/')}/v1/embeddings",
                json={"model": settings.embeddings_model, "input": batch},
                timeout=settings.embeddings_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedder unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise EmbeddingError(f"embedder returned {response.status_code}: {response.text[:200]!r}")

        payload = response.json()
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) != len(batch):
            raise EmbeddingError(f"embedder returned {type(rows).__name__} for {len(batch)} inputs")
        # Sorted by index rather than trusted in order: the API documents an
        # index field, and a server that reorders would corrupt the mapping
        # from text to vector without anything ever looking wrong.
        for row in sorted(rows, key=lambda item: item.get("index", 0)):
            vector = row.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("embedder returned a row with no embedding")
            out.append([float(value) for value in vector])
    return out


def cosine(a: list[float], b: list[float]) -> float:
    """Similarity of two vectors, 0 when either has no magnitude.

    Not guarded against differing lengths here - `zip` would silently compare
    a prefix, which is the failure this is least likely to notice. The caller
    filters by `dims` before getting here, and that is deliberate: two models'
    vectors are incomparable and should never meet.
    """
    if len(a) != len(b):
        raise ValueError(f"cannot compare {len(a)}-dim and {len(b)}-dim vectors")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
