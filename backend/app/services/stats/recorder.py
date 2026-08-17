"""Turns one proxied `/v1/chat/completions` call into the shared
telemetry: one `llm_requests` row plus the Prometheus observations in
`services.stats.prom`. Both the streaming and non-streaming code paths in
`app.api.openai_proxy` accumulate a `RequestOutcome` as they go and hand
it to `record_request` once the call is finished (successfully or not),
so both paths record identically.
"""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.endpoint import ModelEndpoint
from app.models.llm_request import LlmRequest
from app.models.user import User
from app.services.llm.tokenizer import heuristic_token_count
from app.services.stats.prom import (
    LLM_ERRORS_TOTAL,
    LLM_OUTPUT_TPS,
    LLM_REQUEST_DURATION_SECONDS,
    LLM_TOKENS_TOTAL,
    LLM_TTFT_SECONDS,
)


@dataclass
class RequestOutcome:
    """Accumulated over the lifetime of one proxied call by whichever
    code path (streaming/non-streaming, success/failure) is handling it,
    then handed to `record_request` exactly once the call is done."""

    status_code: int = 200
    error_type: str | None = None
    finish_reason: str | None = None
    tool_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_text: str = ""
    ttft_seconds: float | None = None
    started_at: float = field(default_factory=time.monotonic)


def _first_user_message_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
    return ""


def extract_session_ids(
    headers: Any, *, api_key_id: str, messages: list[dict[str, Any]]
) -> tuple[str, str | None]:
    """opencode sends `x-session-affinity`/`x-parent-session-id` by
    default (since a merged upstream PR made that the standard behaviour
    for every OpenAI-compatible provider). When it's missing - an older
    opencode build, or some other OpenAI-compatible client entirely - fall
    back to a hash of the API key and the first user message, which is
    stable across turns of the same conversation without needing any
    client cooperation."""
    session_id = headers.get("x-session-affinity")
    parent_session_id = headers.get("x-parent-session-id") or None
    if session_id:
        return session_id, parent_session_id

    first_message = _first_user_message_text(messages)
    digest = hashlib.sha256(f"{api_key_id}:{first_message}".encode()).hexdigest()
    return f"fallback-{digest[:32]}", parent_session_id


async def record_request(
    db: AsyncSession,
    *,
    user: User,
    # None for answer-synthesis calls made on behalf of a cookie-session
    # user in the web app, who has no API key at all.
    api_key: ApiKey | None,
    endpoint: ModelEndpoint | None,
    requested_model: str,
    reasoning_level: str,
    stream: bool,
    session_id: str,
    parent_session_id: str | None,
    outcome: RequestOutcome,
) -> None:
    duration_seconds = time.monotonic() - outcome.started_at
    reasoning_tokens = heuristic_token_count(outcome.reasoning_text) if outcome.reasoning_text else 0

    cost_usd = 0.0
    if endpoint is not None:
        cost_usd = (
            outcome.prompt_tokens / 1_000_000 * (endpoint.price_per_mtok_in or 0.0)
            + outcome.completion_tokens / 1_000_000 * (endpoint.price_per_mtok_out or 0.0)
        )

    db.add(
        LlmRequest(
            user_id=user.id,
            api_key_id=api_key.id if api_key is not None else None,
            session_id=session_id,
            parent_session_id=parent_session_id,
            model=requested_model,
            endpoint_id=endpoint.id if endpoint is not None else None,
            reasoning_level=reasoning_level,
            stream=stream,
            status_code=outcome.status_code,
            error_type=outcome.error_type,
            finish_reason=outcome.finish_reason,
            tool_call_count=outcome.tool_call_count,
            tokens_prompt=outcome.prompt_tokens,
            tokens_completion=outcome.completion_tokens,
            tokens_reasoning=reasoning_tokens,
            cost_usd=cost_usd,
            ttft_ms=int(outcome.ttft_seconds * 1000) if outcome.ttft_seconds is not None else None,
            duration_ms=int(duration_seconds * 1000),
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()

    if endpoint is None:
        # Routing/auth failed before we ever picked an upstream - nothing
        # meaningful to attribute a per-model Prometheus observation to.
        return

    labels = {"model": endpoint.model_id, "role": endpoint.role, "reasoning_level": reasoning_level}

    if outcome.ttft_seconds is not None:
        LLM_TTFT_SECONDS.labels(**labels).observe(outcome.ttft_seconds)
    LLM_REQUEST_DURATION_SECONDS.labels(**labels).observe(duration_seconds)
    if outcome.completion_tokens > 0 and duration_seconds > 0:
        LLM_OUTPUT_TPS.labels(**labels).observe(outcome.completion_tokens / duration_seconds)

    LLM_TOKENS_TOTAL.labels(model=endpoint.model_id, role=endpoint.role, kind="prompt").inc(outcome.prompt_tokens)
    LLM_TOKENS_TOTAL.labels(model=endpoint.model_id, role=endpoint.role, kind="completion").inc(
        outcome.completion_tokens
    )
    if reasoning_tokens:
        LLM_TOKENS_TOTAL.labels(model=endpoint.model_id, role=endpoint.role, kind="reasoning").inc(reasoning_tokens)

    if outcome.error_type:
        LLM_ERRORS_TOTAL.labels(model=endpoint.model_id, role=endpoint.role, type=outcome.error_type).inc()
