"""Writing an answer over search results, with citations that are checked
rather than trusted.

The model is asked to cite `[n]`; nothing makes it comply. So every `[n]` in
the output is resolved against the hits that were **actually in the prompt**,
and anything out of range is discarded and counted. The citations the UI
renders are therefore correct by construction - a fabricated reference
cannot become a link, because a link only exists where a real hit was found.
"""

import base64
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.schemas.search import Citation, SearchHit
from app.services.llm import chat
from app.services.llm.reasoning import ReasoningStreamParser
from app.services.llm.tokenizer import heuristic_token_count

logger = logging.getLogger("llmhell.answer")

_CITATION = re.compile(r"\[(\d{1,3})\]")

# The model sometimes writes LaTeX math ($\varnothing$, $\times$, etc.) even
# when told not to. Map the common commands to Unicode and unwrap inline math
# so stored answers and history do not expose raw markup.
_LATEX_REPLACEMENTS: dict[str, str] = {
    "varnothing": "⌀",
    "emptyset": "∅",
    "approx": "≈",
    "sim": "∼",
    "times": "×",
    "cdot": "·",
    "pm": "±",
    "mp": "∓",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "degree": "°",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "mu": "µ",
    "Omega": "Ω",
    "omega": "ω",
    "Sigma": "Σ",
    "sigma": "σ",
    "theta": "θ",
    "phi": "φ",
    "Phi": "Φ",
    "pi": "π",
    "infty": "∞",
    "infinity": "∞",
    "ldots": "…",
    "dots": "…",
}


def _sanitize_answer_text(text: str) -> str:
    """Clean LaTeX markup the model occasionally emits."""
    # Inline and block math delimiters: keep the body.
    text = re.sub(r"\$\$([^$]*?)\$\$", r"\1", text)
    text = re.sub(r"\$([^$]*?)\$", r"\1", text)
    # \text{foo} -> foo
    text = re.sub(r"\\text\{([^}]*)\}", r"\1", text)
    # Superscript degree.
    text = re.sub(r"\^\{\\circ\}", "°", text)
    text = re.sub(r"\^\\circ", "°", text)
    # Known commands; leave unknown ones untouched.
    text = re.sub(
        r"\\([a-zA-Z]+)",
        lambda match: _LATEX_REPLACEMENTS.get(match.group(1), match.group(0)),
        text,
    )
    return text


SYSTEM_PROMPT = """\


Rules:
- Cite every claim with the bracketed number of the result it came from, like [2]. \
Cite more than one where more than one supports the claim: [1][3].
- Use ONLY the numbers that appear below. Never invent a number.
- Be concise. Lead with the answer, then the supporting detail.
- Answer in the SAME language the question is written in.
- Some results carry page images, introduced as "Result N, page image M". \
Read those pages: a question about where something sits, what a diagram \
connects, or what a label says is answered from the picture, not from the \
text beside it. Cite a page image as plain [N] - the result number alone, \
with nothing added inside the brackets.
- Answer the question that was asked. If it asks where something is, give the \
position; naming the part instead is not an answer.
- A spreadsheet arrives as one line per row, every filled cell written \
column=value. A cell means nothing on its own: read it together with its row \
label and with the header rows at the top of the sheet, matched by COLUMN \
LETTER. A header row that fills only every few columns is merged - its value \
covers every column up to the next filled one.
- There is usually MORE THAN ONE header row, and the answer is built from all \
of them. If a row reads `R31: B=paint the hull  G=X` and the header rows read \
`R3: C=Sep  M=Oct` and `R4: C=1  D=8  G=18`, then the hull was painted on \
September 18 - column G, the month Sep because Sep covers C to L, the day 18 \
from the day row. Answer with that date, never with a column letter and never \
with the week number.
- Write measurements in plain text, not LaTeX. Use Unicode symbols directly: \
⌀ for diameter, × for multiplication, ° for degrees, ± for plus-minus. \
Never wrap values in $...$ or use \\varnothing, \\times, \\degree, etc.
"""

# You answer questions using ONLY the numbered search results below, which come \
# from the user's Google Workspace, GitLab and internal knowledge base.

# - If the results do not answer the question, say so plainly. Do not fill the gap \
# from your own knowledge - a wrong answer that looks sourced is worse than "I \
# don't know".


@dataclass
class AnswerResult:
    text: str = ""
    reasoning: str = ""
    model: str | None = None
    citations: list[Citation] = field(default_factory=list)
    hits_used: int = 0
    hits_dropped: int = 0
    hallucinated_citations: int = 0
    cited_hit_ids: list[str] = field(default_factory=list)
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


def select_vision_endpoint(endpoints: list[ModelEndpoint], settings: Settings) -> ModelEndpoint | None:
    """The endpoint that reads page images - which is the answer model itself.

    There is deliberately no second model id. **The model that writes the
    answer is the model that has to see the picture.** A separate vision model
    can only ever hand over a *description* of a diagram, and an answer written
    from a description is precisely the failure this path exists to remove: ask
    which side of the MCU the USB port is on, and a transcription that did not
    happen to mention it turns into "the documents contain no information about
    that" while the board sits legible in the file.

    A deployment whose answer model is text-only turns pictures off with
    `answer_image_hits = 0` (the answer prompt) and `vision_max_pages = 0`
    (page transcription). Sending an image to such a model does not degrade,
    it fails as a 400 per page - so the switch is kept. It is just not a
    second model.
    """
    return select_endpoint(endpoints, settings)


def render_hit(index: int, hit: SearchHit, *, snippet_chars: int, grid_chars: int | None = None) -> str:
    """One numbered block. The number is what the model is told to cite.

    A spreadsheet gets `grid_chars` instead, and needs to: its source has
    already trimmed it to that budget while keeping the header band and the
    legend, and cutting it again here would take the header rows off the top
    of the only thing that says what a column means.
    """
    header = f"[{index}] source={hit.source} | {hit.title}"
    if hit.url:
        header += f" | {hit.url}"
    if hit.timestamp:
        header += f" | {hit.timestamp.date().isoformat()}"
    limit = grid_chars if hit.snippet_format == "grid" and grid_chars else snippet_chars
    snippet = (hit.snippet or "")[:limit]
    return f"{header}\n{snippet}"


# How many prior turns to carry, and how much of each. Bounded because the
# alternative is a conversation that silently squeezes out the search results
# it is supposed to be answering from - the hits are the point, the history
# is context.
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 1500


def _history_messages(history: list[Any] | None) -> list[dict[str, str]]:
    if not history:
        return []
    recent = history[-MAX_HISTORY_TURNS:]
    messages: list[dict[str, str]] = []
    for turn in recent:
        role = getattr(turn, "role", None) or (turn.get("role") if isinstance(turn, dict) else None)
        content = getattr(turn, "content", None) or (turn.get("content") if isinstance(turn, dict) else None)
        if role not in ("user", "assistant") or not content:
            continue
        messages.append({"role": role, "content": str(content)[:MAX_HISTORY_CHARS]})
    return messages


def build_prompt(
    question: str,
    hits: list[SearchHit],
    endpoint: ModelEndpoint,
    *,
    settings: Settings,
    history: list[Any] | None = None,
    images: dict[str, list[bytes]] | None = None,
) -> tuple[list[dict[str, Any]], list[SearchHit]]:
    """Pack as many hits as fit, and report which ones made it.

    Returns the messages plus the hits actually included, in order - the
    caller needs that list to resolve citations, because `[3]` means "the
    third hit in the prompt", not "the third search result".

    `images` maps a hit id to page pictures, which are attached to the same
    turn as the text. One model, one pass: it reads the diagram and the
    search results together rather than answering from somebody else's
    description of the diagram.
    """
    history_messages = _history_messages(history)
    images = images or {}

    budget = endpoint.ctx_window - settings.answer_max_output_tokens - settings.answer_ctx_reserve_tokens
    overhead = heuristic_token_count(
        SYSTEM_PROMPT + question + "".join(message["content"] for message in history_messages)
    )
    remaining = max(0, budget - overhead)

    included: list[SearchHit] = []
    blocks: list[str] = []
    for hit in hits:
        block = render_hit(
            len(included) + 1,
            hit,
            snippet_chars=settings.answer_snippet_chars,
            grid_chars=settings.answer_sheet_chars,
        )
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
    turn = f"Question: {question}\n\nSearch results:\n\n{context}"

    # Each picture names the result it belongs to, so the model can tell
    # which snippet the page goes with and cite it as that result.
    attachments: list[dict[str, Any]] = []
    for position, hit in enumerate(included, start=1):
        for offset, page in enumerate(images.get(hit.id, []), start=1):
            encoded = base64.b64encode(page).decode("ascii")
            # "Result N, page image M", deliberately not "[N] page image M".
            # A bracketed number here is the citation syntax, and the model
            # copied the label wholesale - "[1 (page image 2)]" - which the
            # citation parser does not recognise, so a correct answer came
            # back with no citations attached at all.
            attachments.append(
                {"type": "text", "text": f"Result {position}, page image {offset}:"}
            )
            attachments.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})

    # A plain string when there is nothing to attach: a text-only server
    # should not be handed the multimodal list form for a question that never
    # needed it.
    content: Any = [{"type": "text", "text": turn}, *attachments] if attachments else turn

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        # History sits between the system prompt and the current turn, so the
        # results are the last thing the model reads.
        *history_messages,
        {"role": "user", "content": content},
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
        citations.append(Citation(n=n, hit_id=hit.id, title=hit.title, url=hit.url, source=hit.source))

    citations.sort(key=lambda citation: citation.n)
    return citations, hallucinated


async def synthesize(
    question: str,
    hits: list[SearchHit],
    endpoint: ModelEndpoint | None,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    history: list[Any] | None = None,
    images: dict[str, list[bytes]] | None = None,
) -> AnswerResult:
    """Non-streaming answer."""
    if endpoint is None:
        return AnswerResult(error="no model endpoint is registered")

    messages, included = build_prompt(question, hits, endpoint, settings=settings, history=history, images=images)
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

    text = _sanitize_answer_text(completion.content)
    citations, hallucinated = extract_citations(text, included)
    return AnswerResult(
        text=text,
        reasoning=completion.reasoning,
        model=endpoint.model_id,
        citations=citations,
        hits_used=len(included),
        hits_dropped=len(hits) - len(included),
        hallucinated_citations=hallucinated,
        cited_hit_ids=[citation.hit_id for citation in citations],
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
    history: list[Any] | None = None,
    images: dict[str, list[bytes]] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Yield ("reasoning"|"token"|"done"|"error", payload).

    Splits reasoning from content using the endpoint's own configured
    profile, so the UI can collapse the thinking instead of rendering it as
    part of the answer.
    """
    if endpoint is None:
        yield "error", {"message": "no model endpoint is registered"}
        return

    messages, included = build_prompt(question, hits, endpoint, settings=settings, history=history, images=images)
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

    text = _sanitize_answer_text("".join(text_parts))
    citations, hallucinated = extract_citations(text, included)
    yield (
        "done",
        AnswerResult(
            text=text,
            reasoning="".join(reasoning_parts),
            model=endpoint.model_id,
            citations=citations,
            hits_used=len(included),
            hits_dropped=len(hits) - len(included),
            hallucinated_citations=hallucinated,
            cited_hit_ids=[citation.hit_id for citation in citations],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )
