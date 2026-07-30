"""Token counting against a vLLM endpoint's `/tokenize` route.

vLLM's OpenAI-compatible server exposes `/tokenize` at the server root,
not under `/v1`, so the endpoint's `base_url` (which points at `.../v1`)
has that suffix stripped before the request. Falls back to a length
heuristic when the endpoint is unreachable or doesn't implement it, so
context counting keeps working (with a coarser estimate) even then.
"""

import hashlib
import logging
from collections import OrderedDict

import httpx

from app.models.endpoint import ModelEndpoint

logger = logging.getLogger("llmhell.tokenizer")

_CACHE_MAX_SIZE = 4096
_cache: "OrderedDict[tuple[str, str], int]" = OrderedDict()


def _cache_get(endpoint_id: str, text: str) -> int | None:
    key = (endpoint_id, hashlib.sha256(text.encode("utf-8")).hexdigest())
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


def _cache_put(endpoint_id: str, text: str, count: int) -> None:
    key = (endpoint_id, hashlib.sha256(text.encode("utf-8")).hexdigest())
    _cache[key] = count
    _cache.move_to_end(key)
    if len(_cache) > _CACHE_MAX_SIZE:
        _cache.popitem(last=False)


def tokenize_root_url(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/v1") + "/tokenize"


def heuristic_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / 3.5))


async def count_tokens(endpoint: ModelEndpoint, text: str, http_client: httpx.AsyncClient) -> int:
    if not text:
        return 0

    cached = _cache_get(endpoint.id, text)
    if cached is not None:
        return cached

    try:
        response = await http_client.post(
            tokenize_root_url(endpoint.base_url),
            json={"model": endpoint.model_id, "prompt": text},
            headers={"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {},
            timeout=10.0,
        )
        response.raise_for_status()
        body = response.json()
        count = int(body.get("count") if body.get("count") is not None else len(body.get("tokens", [])))
        if count <= 0:
            raise ValueError("tokenizer returned zero tokens")
    except Exception as exc:  # noqa: BLE001 - any failure falls back to the heuristic
        logger.warning("tokenize() failed for endpoint %s, using heuristic: %s", endpoint.name, exc)
        count = heuristic_token_count(text)

    _cache_put(endpoint.id, text, count)
    return count
