"""Streaming chat-completion client for a single vLLM/OpenAI-compatible
model endpoint. Wraps reasoning-level request building, reasoning/content
stream splitting, and native tool-call accumulation behind a normalized
event stream so callers (agent loop, endpoint probe) never touch the raw
OpenAI SDK objects directly.
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Union

import httpx
from openai import AsyncOpenAI

from app.models.endpoint import ModelEndpoint
from app.services.llm.reasoning import ReasoningStreamParser, build_extra_body
from app.services.llm.toolcalls import ToolSpec, build_native_tools

logger = logging.getLogger("llmhell.llm_client")


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ContentDelta:
    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    call_id: str | None
    name: str | None
    arguments_delta: str


@dataclass(frozen=True)
class ToolCallComplete:
    call_id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass(frozen=True)
class UsageInfo:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class FinishEvent:
    reason: str


@dataclass(frozen=True)
class ErrorEvent:
    message: str


LLMEvent = Union[
    ReasoningDelta, ContentDelta, ToolCallDelta, ToolCallComplete, UsageInfo, FinishEvent, ErrorEvent
]


@dataclass
class _ToolCallAccumulator:
    call_id: str | None = None
    name: str | None = None
    arguments: str = field(default="")


class LLMClient:
    def __init__(self, endpoint: ModelEndpoint, http_client: httpx.AsyncClient | None = None):
        self.endpoint = endpoint
        self._client = AsyncOpenAI(
            base_url=endpoint.base_url,
            api_key=endpoint.api_key or "not-needed",
            http_client=http_client,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        reasoning_level: str,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMEvent]:
        extra_body = build_extra_body(self.endpoint.reasoning_profile, reasoning_level)

        kwargs: dict[str, Any] = {
            "model": self.endpoint.model_id,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": extra_body,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools and self.endpoint.tools_mode == "native":
            kwargs["tools"] = build_native_tools(tools)

        parser = ReasoningStreamParser(self.endpoint.reasoning_profile.get("parse", {}))
        accumulators: dict[int, _ToolCallAccumulator] = {}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.usage is not None:
                    yield UsageInfo(
                        prompt_tokens=chunk.usage.prompt_tokens or 0,
                        completion_tokens=chunk.usage.completion_tokens or 0,
                    )

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta.model_dump(exclude_none=True) if choice.delta else {}

                parsed = parser.feed(delta)
                if parsed.reasoning_text:
                    yield ReasoningDelta(parsed.reasoning_text)
                if parsed.content_text:
                    yield ContentDelta(parsed.content_text)

                for tool_call_delta in delta.get("tool_calls") or []:
                    idx = tool_call_delta.get("index", 0)
                    acc = accumulators.setdefault(idx, _ToolCallAccumulator())
                    if tool_call_delta.get("id"):
                        acc.call_id = tool_call_delta["id"]
                    fn = tool_call_delta.get("function") or {}
                    if fn.get("name"):
                        acc.name = fn["name"]
                    args_piece = fn.get("arguments") or ""
                    acc.arguments += args_piece
                    yield ToolCallDelta(
                        index=idx, call_id=acc.call_id, name=acc.name, arguments_delta=args_piece
                    )

                if choice.finish_reason:
                    tail = parser.flush()
                    if tail.content_text:
                        yield ContentDelta(tail.content_text)

                    if choice.finish_reason == "tool_calls":
                        for idx in sorted(accumulators):
                            acc = accumulators[idx]
                            if not acc.name or not acc.call_id:
                                continue
                            try:
                                parsed_args = json.loads(acc.arguments) if acc.arguments else {}
                            except json.JSONDecodeError:
                                parsed_args = {}
                            yield ToolCallComplete(
                                call_id=acc.call_id,
                                name=acc.name,
                                arguments=parsed_args,
                                raw_arguments=acc.arguments,
                            )

                    yield FinishEvent(choice.finish_reason)
        except Exception as exc:  # noqa: BLE001 - surface as an event, caller decides how to react
            logger.exception("LLM stream failed for endpoint %s", self.endpoint.name)
            yield ErrorEvent(str(exc))
