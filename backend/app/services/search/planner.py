"""Turning what a person asked into what a document would actually say.

The search was context-blind and literal, and both halves showed. Asked "чи
присутній тут гіроскоп" - a follow-up, in the same chat, about a board already
under discussion - it found nothing: `тут` refers to something only the
previous turn knows about, and `гіроскоп` appears nowhere in a datasheet that
says `IMU: MPU6000`. Rephrased as "чи присутній гіроскоп на платі F722" the
same question answered correctly, because `F722` happened to be a literal
match. Whether the search worked depended on the user guessing a token from
the document.

So the model plans the search. It reads the conversation, resolves what the
question is actually about, and writes queries in the vocabulary the corpus
uses rather than the vocabulary the question used. Several of them, because
one phrasing is a guess and three are a search.

The queries are run concurrently and their results merged, so this costs
latency once rather than once per query.

Every failure here falls back to the question as typed, which is exactly the
old behaviour: planning can improve a search and must never be able to
prevent one.
"""

import json
import logging
import re
from typing import Any

import httpx

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.services.llm import chat

logger = logging.getLogger("llmhell.planner")

# Same shape as the text2sql prompt: one JSON object, nothing else. A model
# that will not follow that is a model whose plan we do not want.
#
# The braces in the example are DOUBLED because this string is `.format()`ed
# for max_queries, and a single brace there is a format placeholder - which
# fails with KeyError on the example itself.
SYSTEM_PROMPT = """\
You turn a user's question into search queries for a federated document \
search over Google Drive, Gmail, GitLab and an internal knowledge base.

Reply with a single JSON object and nothing else:

{{"queries": ["...", "..."]}}

Rules:
- Write between 1 and {max_queries} queries, most likely to succeed first.
- The conversation supplies only what the question LEAVES OUT, which is \
usually the subject. Take the NAME of the thing from it and nothing else. The \
topic always comes from the question itself: if the question asks about a \
gyroscope, every query is about a gyroscope, even when the previous turn was \
about a USB port.
- The last question is the only question. Never search for the previous one \
again.
- Say the thing's name in every query, never "it", "this" or "here".
- The documents are mostly ENGLISH technical material. Translate, and use the \
words a document would print rather than the words a person would say: a \
gyroscope appears as "IMU", "gyro" or a part number like "MPU6000"; a \
processor as "MCU" or "STM32".
- Prefer identifiers, part numbers, file names and exact labels. Those are \
what match; adjectives and verbs are not.
- Make the queries DIFFERENT from each other. Three phrasings of one guess is \
one guess.
- Keep each query short - a few words, not a sentence.
"""

# Bounded because each query is a full fan-out across every source. Three
# distinct phrasings is where the returns flatten; beyond that they mostly
# restate one another.
MAX_QUERIES = 3
MAX_TOKENS = 300

# Same reasoning as text2sql: this is a mechanical rewrite, and a model that
# deliberates over it spends its budget and returns nothing. Both dialects,
# each inert where it does not apply.
_NO_THINKING = {
    "chat_template_kwargs": {"enable_thinking": False},
    "reasoning": {"enabled": False},
}

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_queries(content: str, *, limit: int) -> list[str]:
    """The planned queries, or [] if the model did not produce any.

    Tolerates a fenced block, because a model told to emit bare JSON will
    sometimes wrap it anyway and throwing that away would waste a good plan
    over punctuation.
    """
    stripped = _FENCE.sub("", content or "").strip()
    if not stripped:
        return []

    try:
        decoded = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        logger.info("planner did not return JSON: %.200s", stripped)
        return []

    raw: Any = decoded.get("queries") if isinstance(decoded, dict) else decoded
    if not isinstance(raw, list):
        return []

    queries: list[str] = []
    for entry in raw:
        text = str(entry).strip()
        # Deduplicated case-insensitively: two spellings of one query buy
        # nothing and cost a whole fan-out.
        if text and text.lower() not in {existing.lower() for existing in queries}:
            queries.append(text)
        if len(queries) >= limit:
            break
    return queries


def build_prompt(question: str, history: list[Any] | None, *, max_queries: int) -> list[dict[str, str]]:
    turns: list[str] = []
    for turn in (history or [])[-6:]:
        role = getattr(turn, "role", None) or (turn.get("role") if isinstance(turn, dict) else None)
        content = getattr(turn, "content", None) or (turn.get("content") if isinstance(turn, dict) else None)
        if role in ("user", "assistant") and content:
            turns.append(f"{role}: {str(content)[:600]}")

    conversation = "\n".join(turns) if turns else "(this is the first question)"
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(max_queries=max_queries)},
        {"role": "user", "content": f"Conversation so far:\n{conversation}\n\nQuestion: {question}"},
    ]


async def plan_queries(
    question: str,
    *,
    history: list[Any] | None,
    endpoint: ModelEndpoint | None,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> list[str]:
    """Search queries for this question, always including something runnable.

    The question as typed is always in the list - a plan is an addition, not a
    replacement. If the model's idea is better it wins on the merged results;
    if the model is unavailable the search is exactly what it was before.
    """
    limit = max(1, settings.search_plan_queries)
    if endpoint is None or settings.search_plan_queries <= 0:
        return [question]

    try:
        result = await chat.complete(
            endpoint,
            build_prompt(question, history, max_queries=limit),
            http_client=http_client,
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=settings.search_plan_timeout_seconds,
            extra_body=_NO_THINKING,
        )
    except chat.ChatError as exc:
        logger.info("query planning failed, using the question as typed: %s", exc)
        return [question]

    planned = parse_queries(result.content, limit=limit)
    if not planned:
        return [question]

    # The original goes first: it is the one phrasing we know the user meant,
    # and on a corpus that happens to share their vocabulary it is the best
    # query there is.
    queries = [question]
    for candidate in planned:
        if candidate.lower() != question.lower():
            queries.append(candidate)

    logger.info("planned queries: %s", queries)
    return queries[: limit + 1]
