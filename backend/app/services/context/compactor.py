"""Keeps a run's context under budget: truncates oversized tool output
inline, and - once usage crosses the configured threshold - collapses the
older portion of the conversation history into a single summary message
via a cheap non-reasoning planner call.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.endpoint import ModelEndpoint
from app.models.run import Compaction
from app.services.context.counter import ContextUsage
from app.services.llm.client import ContentDelta, LLMClient

SUMMARY_PROMPT_PREFIX = (
    "Summarize the following part of an ongoing coding-agent conversation "
    "into a compact brief for a model that will continue the work. Cover: "
    "what has been done, what broke and how it was resolved (or wasn't), "
    "which files were touched, and any decisions that must be remembered. "
    "Be concrete and terse - this replaces the raw transcript below, not a "
    "narrative summary of it.\n\n---\n\n"
)


def truncate_tool_output(text: str, max_lines: int = 200) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text

    head_n = max_lines // 2
    tail_n = max_lines - head_n
    omitted = len(lines) - head_n - tail_n
    return "\n".join(
        [
            *lines[:head_n],
            f"... [{omitted} lines omitted] ...",
            *lines[-tail_n:],
        ]
    )


def should_compact(usage: ContextUsage, threshold: float) -> bool:
    return usage.fraction_used >= threshold


@dataclass
class CompactionResult:
    summary: str
    remaining_history: list[dict[str, Any]]
    trigger: str


async def summarize_history(
    history: list[dict[str, Any]],
    planner_endpoint: ModelEndpoint,
    http_client: httpx.AsyncClient,
) -> str:
    transcript = "\n\n".join(f"[{m.get('role')}] {m.get('content', '')}" for m in history)
    client = LLMClient(planner_endpoint, http_client=http_client)

    content = ""
    async for event in client.stream_chat(
        messages=[{"role": "user", "content": SUMMARY_PROMPT_PREFIX + transcript}],
        reasoning_level="off",
    ):
        if isinstance(event, ContentDelta):
            content += event.text

    return content.strip()


async def compact_if_needed(
    *,
    usage: ContextUsage,
    threshold: float,
    history: list[dict[str, Any]],
    existing_summary: str | None,
    planner_endpoint: ModelEndpoint,
    http_client: httpx.AsyncClient,
    history_fraction_to_summarize: float = 0.4,
) -> CompactionResult | None:
    if not should_compact(usage, threshold):
        return None

    split = max(1, int(len(history) * history_fraction_to_summarize))
    older, newer = history[:split], history[split:]
    if not older:
        return None

    new_piece = await summarize_history(older, planner_endpoint, http_client)
    combined = f"{existing_summary}\n\n{new_piece}" if existing_summary else new_piece

    return CompactionResult(summary=combined, remaining_history=newer, trigger="threshold")


async def persist_compaction(
    db: AsyncSession,
    *,
    run_id: str,
    trigger: str,
    tokens_before: int,
    tokens_after: int,
    summary: str,
) -> Compaction:
    compaction = Compaction(
        run_id=run_id,
        trigger=trigger,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        summary=summary,
        created_at=datetime.now(timezone.utc),
    )
    db.add(compaction)
    await db.flush()
    return compaction
