"""Writing an answer over search results, with citations that are checked
rather than trusted.

The model is asked to cite `[n]`; nothing makes it comply. So every `[n]` in
the output is resolved against the hits that were **actually in the prompt**,
and anything out of range is discarded and counted. The citations the UI
renders are therefore correct by construction - a fabricated reference
cannot become a link, because a link only exists where a real hit was found.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.schemas.search import Citation, SearchHit
from app.services.llm import chat
from app.services.llm.reasoning import ReasoningStreamParser
from app.services.llm.tokenizer import heuristic_token_count

logger = logging.getLogger("llmhell.answer")

_CITATION = re.compile(r"\[(\d{1,3})\]")

SYSTEM_PROMPT = """\
You answer questions using ONLY the numbered search results below, which come \
from the user's Google Workspace, GitLab and internal knowledge base.

Rules:
- Cite every claim with the bracketed number of the result it came from, like [2]. \
Cite more than one where more than one supports the claim: [1][3].
- Use ONLY the numbers that appear below. Never invent a number.
- If the results do not answer the question, say so plainly. Do not fill the gap \
from your own knowledge - a wrong answer that looks sourced is worse than "I \
don't know".
- Be concise. Lead with the answer, then the supporting detail.
"""


@dataclass
class AnswerResult:
    text: str = ""
    reasoning: str = ""
    model: str | None = None
    citations: list[Citation] = field(default_factory=list)
    hits_used: int = 0
    hits_dropped: int = 0
    hallucinated_citations: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None


def select_endpoint(endpoints: list[ModelEndpoint], settings: Settings) -> ModelEndpoint | None:
    """The endpoint that writes answers, or None when none is registered.

    None is not an error: search still returns its hits, and the UI shows the
    result list without an answer. Tying the result list to the LLM being up
    would make a working federated search look broken.
    """
    enabled = [endpoint for endpoint in endpoints if endpoint.enabled]
    if not enabled:
        return None
    if settings.answer_model_id:
        for endpoint in enabled:
            if endpoint.model_id == settings.answer_model_id:
                return endpoint
        logger.warning(
            "ANSWER_MODEL_ID=%r matches no enabled endpoint; using %r instead",
            settings.answer_model_id,
            enabled[0].model_id,
        )
    return enabled[0]


def render_hit(index: int, hit: SearchHit, *, snippet_chars: int) -> str:
    """One numbered block. The number is what the model is told to cite."""
    header = f"[{index}] source={hit.source} | {hit.title}"
    if hit.url:
        header += f" | {hit.url}"
    if hit.timestamp:
        header += f" | {hit.timestamp.date().isoformat()}"
    snippet = (hit.snippet or "")[:snippet_chars]
    return f"{header}\n{snippet}"


def build_prompt(
    question: str,
    hits: list[SearchHit],
    endpoint: ModelEndpoint,
    *,
    settings: Settings,
) -> tuple[list[dict[str, str]], list[SearchHit]]:
    """Pack as many hits as fit, and report which ones made it.

    Returns the messages plus the hits actually included, in order - the
    caller needs that list to resolve citations, because `[3]` means "the
    third hit in the prompt", not "the third search result".
    """
    budget = endpoint.ctx_window - settings.answer_max_output_tokens - settings.answer_ctx_reserve_tokens
    overhead = heuristic_token_count(SYSTEM_PROMPT + question)
    remaining = max(0, budget - overhead)

    included: list[SearchHit] = []
    blocks: list[str] = []
    for hit in hits:
        block = render_hit(len(included) + 1, hit, snippet_chars=settings.answer_snippet_chars)
        # The character heuristic rather than the endpoint's own /tokenize:
        # this runs per candidate hit, and an HTTP round-trip each would cost
        # far more than the packing decision is worth. The reserve in
        # answer_ctx_reserve_tokens is what absorbs the imprecision.
        cost = heuristic_token_count(block)
        # `included and` so the first hit always goes in - a single oversized
        # document should be truncated by snippet_chars, not silently drop
        # the entire answer context.
        if included and cost > remaining:
            break
        blocks.append(block)
        included.append(hit)
        remaining -= cost

    context = "\n\n".join(blocks) if blocks else "(no results were found)"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nSearch results:\n\n{context}"},
    ]
    return messages, included


def extract_citations(text: str, hits: list[SearchHit]) -> tuple[list[Citation], int]:
    """Resolve `[n]` references against the hits that were in the prompt.

    Returns (citations, hallucinated_count). Out-of-range and zero indices
    are dropped rather than clamped: silently pointing `[9]` at hit 4 would
    manufacture a citation the model never made.
    """
    citations: list[Citation] = []
    seen: set[int] = set()
    hallucinated = 0

    for match in _CITATION.finditer(text or ""):
        n = int(match.group(1))
        if n < 1 or n > len(hits):
            hallucinated += 1
            continue
        if n in seen:
            continue
        seen.add(n)
        hit = hits[n - 1]
        citations.append(
            Citation(n=n, hit_id=hit.id, title=hit.title, url=hit.url, source=hit.source)
        )

    citations.sort(key=lambda citation: citation.n)
    return citations, hallucinated


async def synthesize(
    question: str,
    hits: list[SearchHit],
    endpoint: ModelEndpoint | None,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> AnswerResult:
    """Non-streaming answer."""
    if endpoint is None:
        return AnswerResult(error="no model endpoint is registered")

    messages, included = build_prompt(question, hits, endpoint, settings=settings)
    try:
        completion = await chat.complete(
            endpoint,
            messages,
            http_client=http_client,
            max_tokens=settings.answer_max_output_tokens,
            temperature=settings.answer_temperature,
        )
    except chat.ChatError as exc:
        return AnswerResult(model=endpoint.model_id, error=str(exc))

    citations, hallucinated = extract_citations(completion.content, included)
    return AnswerResult(
        text=completion.content,
        reasoning=completion.reasoning,
        model=endpoint.model_id,
        citations=citations,
        hits_used=len(included),
        hits_dropped=len(hits) - len(included),
        hallucinated_citations=hallucinated,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
    )


async def synthesize_stream(
    question: str,
    hits: list[SearchHit],
    endpoint: ModelEndpoint | None,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> AsyncIterator[tuple[str, Any]]:
    """Yield ("reasoning"|"token"|"done"|"error", payload).

    Splits reasoning from content using the endpoint's own configured
    profile, so the UI can collapse the thinking instead of rendering it as
    part of the answer.
    """
    if endpoint is None:
        yield "error", {"message": "no model endpoint is registered"}
        return

    messages, included = build_prompt(question, hits, endpoint, settings=settings)
    parser = ReasoningStreamParser((endpoint.reasoning_profile or {}).get("parse", {}))

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    prompt_tokens = completion_tokens = 0

    try:
        async for event in chat.stream_deltas(
            endpoint,
            messages,
            http_client=http_client,
            max_tokens=settings.answer_max_output_tokens,
            temperature=settings.answer_temperature,
        ):
            usage = event.get("usage")
            if usage:
                prompt_tokens = usage.get("prompt_tokens") or prompt_tokens
                completion_tokens = usage.get("completion_tokens") or completion_tokens

            choices = event.get("choices") or []
            if not choices:
                continue
            parsed = parser.feed(choices[0].get("delta") or {})
            if parsed.reasoning_text:
                reasoning_parts.append(parsed.reasoning_text)
                yield "reasoning", {"text": parsed.reasoning_text}
            if parsed.content_text:
                text_parts.append(parsed.content_text)
                yield "token", {"text": parsed.content_text}
    except chat.ChatError as exc:
        yield "error", {"message": str(exc)}
        return

    # The proxy never calls this, and a truncated stream loses its tail as a
    # result; here it matters because the tail may contain a citation.
    tail = parser.flush()
    if tail.content_text:
        text_parts.append(tail.content_text)
        yield "token", {"text": tail.content_text}

    text = "".join(text_parts)
    citations, hallucinated = extract_citations(text, included)
    yield "done", AnswerResult(
        text=text,
        reasoning="".join(reasoning_parts),
        model=endpoint.model_id,
        citations=citations,
        hits_used=len(included),
        hits_dropped=len(hits) - len(included),
        hallucinated_citations=hallucinated,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
