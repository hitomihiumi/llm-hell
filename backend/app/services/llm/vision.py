"""Turning a rendered page into text, with a vision model.

The model here is a **transcriber, not an answerer**. It never sees the user's
question and never decides anything; it converts one page image into prose and
that prose joins the document's text layer as an ordinary snippet. Everything
downstream - ranking, citations, the answer prompt, the viewer - is unchanged
and does not know a vision model exists.

That choice buys three things. The answer model stays the one that reasons, so
answers do not change character depending on which pages happened to be
illustrated. The description is cacheable per page, because it does not depend
on the question. And a vision model that is down costs the illustrations, not
the answer.
"""

import asyncio
import base64
import logging

import httpx

from app.models.endpoint import ModelEndpoint
from app.services.llm import chat

logger = logging.getLogger("llmhell.vision")

# Deliberately about transcription, not interpretation. "Describe this image"
# invites a model to editorialise; this asks it to read the page out.
PROMPT = (
    "This is one page of a PDF. Transcribe it for a reader who cannot see it.\n"
    "\n"
    "- Read out every label, number, pin name and caption exactly as printed.\n"
    "- Describe diagrams, wiring, tables and screenshots in terms of what they "
    "connect or contain, not how they look.\n"
    "- Do not summarise, do not add anything that is not on the page, and do "
    "not guess at a value you cannot read.\n"
    "- If the page is blank or purely decorative, reply with exactly: (nothing)"
)

# What a page transcription is allowed to cost. A page of dense pinouts is
# long, but a model that has started repeating itself should be cut off rather
# than allowed to fill the answer prompt with noise.
MAX_TOKENS = 1200

# Marks a page that carried nothing worth keeping, so the caller can drop it
# instead of feeding "(nothing)" into a prompt.
EMPTY = "(nothing)"


def build_messages(image: bytes, *, mime: str = "image/jpeg") -> list[dict]:
    """One page, as an OpenAI-style multimodal message.

    The image travels as a data: URI rather than a URL because the server has
    no route to this process's filesystem, and putting a rendered page behind
    a public URL to show it to a model on the same machine would be absurd.
    """
    encoded = base64.b64encode(image).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        }
    ]


async def describe_page(
    endpoint: ModelEndpoint,
    image: bytes,
    *,
    http_client: httpx.AsyncClient,
    timeout: float = 180.0,
) -> str:
    """One page's transcription, or "" if the model could not produce one.

    Failure is a missing illustration, never an error: the document's text
    layer is already in hand by the time this runs, and losing a whole search
    because a page would not render is the wrong trade.
    """
    try:
        result = await chat.complete(
            endpoint,
            build_messages(image),
            http_client=http_client,
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=timeout,
        )
    except chat.ChatError as exc:
        logger.info("vision call failed: %s", exc)
        return ""

    text = (result.content or "").strip()
    if not text or text.lower().startswith(EMPTY):
        return ""
    return text


async def describe_pages(
    endpoint: ModelEndpoint,
    pages: dict[int, bytes],
    *,
    http_client: httpx.AsyncClient,
    timeout: float = 180.0,
    concurrency: int = 1,
) -> dict[int, str]:
    """Page index -> transcription, for the pages that produced one.

    One at a time by default, and that default was bought the hard way. An
    earlier version fanned all four pages out at once on the reasoning that
    "the server bounds its own concurrency" - which a hosted cluster does and
    a local server does not. Ollama serves one slot on a 6 GB card: three of
    the four requests came back as transport failures and the document was
    indexed with one page of four, silently, because a failed page is a
    missing illustration rather than an error.

    Raise it for a server that really is parallel; the cost of getting it
    wrong is invisible, which is why the safe value is the default.
    """
    if not pages:
        return {}

    limit = asyncio.Semaphore(max(1, concurrency))

    async def one(index: int) -> str:
        async with limit:
            return await describe_page(
                endpoint, pages[index], http_client=http_client, timeout=timeout
            )

    indices = sorted(pages)
    results = await asyncio.gather(*(one(index) for index in indices), return_exceptions=True)

    described: dict[int, str] = {}
    for index, result in zip(indices, results, strict=True):
        if isinstance(result, BaseException):
            logger.info("vision call for page %d raised: %s", index + 1, result)
            continue
        if result:
            described[index] = result
    return described


def merge(text_layer: str, described: dict[int, str]) -> str:
    """The document as the answer model will see it.

    The text layer comes first and is never altered - it is exact, and a
    model's reading of a page is not. Transcriptions follow, each labelled
    with its page, so an answer quoting one can be traced back to a page
    number rather than to "somewhere in the PDF".
    """
    parts = [text_layer.strip()] if text_layer.strip() else []
    for index in sorted(described):
        parts.append(f"[page {index + 1}, read from the page image]\n{described[index].strip()}")
    return "\n\n".join(parts)
