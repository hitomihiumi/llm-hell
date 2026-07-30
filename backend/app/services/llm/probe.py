"""The "check endpoint" admin action: probes a real vLLM endpoint against
its currently configured reasoning_profile and tools_mode, and reports
what actually came back. This is the mechanism for resolving the
unknowns around reasoning delivery and tool-call support without a code
change - the admin reads the report and adjusts the profile in the UI.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.models.endpoint import ModelEndpoint
from app.services.llm.reasoning import REASONING_LEVELS, build_extra_body
from app.services.llm.tokenizer import tokenize_root_url
from app.services.llm.toolcalls import ToolSpec, build_native_tools

logger = logging.getLogger("llmhell.probe")

_PROBE_MESSAGE = [{"role": "user", "content": "Reply with the single word OK and nothing else."}]
_PROBE_TOOL = ToolSpec(
    name="ping",
    description="A no-op probe tool used only to check whether this endpoint supports native tool calls.",
    parameters={"type": "object", "properties": {}},
)


@dataclass
class LevelProbeResult:
    level: str
    ok: bool
    reasoning_content_present: bool
    inline_tags_present: bool
    content_sample: str
    error: str | None = None


@dataclass
class EndpointProbeReport:
    models_ok: bool
    models_error: str | None
    tokenize_ok: bool
    tokenize_error: str | None
    levels: list[LevelProbeResult]
    native_tools_supported: bool
    native_tools_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "models_ok": self.models_ok,
            "models_error": self.models_error,
            "tokenize_ok": self.tokenize_ok,
            "tokenize_error": self.tokenize_error,
            "levels": [asdict(level) for level in self.levels],
            "native_tools_supported": self.native_tools_supported,
            "native_tools_error": self.native_tools_error,
        }


async def _check_models(client: AsyncOpenAI) -> tuple[bool, str | None]:
    try:
        await client.models.list()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def _check_tokenize(endpoint: ModelEndpoint, http_client: httpx.AsyncClient) -> tuple[bool, str | None]:
    try:
        response = await http_client.post(
            tokenize_root_url(endpoint.base_url),
            json={"model": endpoint.model_id, "prompt": "hello world"},
            headers={"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {},
        )
        response.raise_for_status()
        body = response.json()
        if "count" not in body and "tokens" not in body:
            return False, "response missing 'count'/'tokens' fields"
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def _check_level(client: AsyncOpenAI, endpoint: ModelEndpoint, level: str) -> LevelProbeResult:
    extra_body = build_extra_body(endpoint.reasoning_profile, level)
    parse_cfg = endpoint.reasoning_profile.get("parse", {})
    field_name = parse_cfg.get("field", "reasoning_content")
    open_tag = (parse_cfg.get("tags") or ["<think>", "</think>"])[0]

    try:
        response = await client.chat.completions.create(
            model=endpoint.model_id,
            messages=_PROBE_MESSAGE,
            stream=False,
            max_tokens=200,
            extra_body=extra_body,
        )
        message = response.choices[0].message.model_dump(exclude_none=True)
        content = message.get("content") or ""
        reasoning_present = bool(message.get(field_name))
        tags_present = open_tag in content
        return LevelProbeResult(
            level=level,
            ok=True,
            reasoning_content_present=reasoning_present,
            inline_tags_present=tags_present,
            content_sample=content[:200],
        )
    except Exception as exc:  # noqa: BLE001
        return LevelProbeResult(
            level=level,
            ok=False,
            reasoning_content_present=False,
            inline_tags_present=False,
            content_sample="",
            error=str(exc),
        )


async def _check_native_tools(client: AsyncOpenAI, endpoint: ModelEndpoint) -> tuple[bool, str | None]:
    try:
        response = await client.chat.completions.create(
            model=endpoint.model_id,
            messages=[{"role": "user", "content": "Call the ping tool."}],
            stream=False,
            max_tokens=100,
            tools=build_native_tools([_PROBE_TOOL]),
        )
        message = response.choices[0].message.model_dump(exclude_none=True)
        return bool(message.get("tool_calls")), None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def check_endpoint(
    endpoint: ModelEndpoint, http_client: httpx.AsyncClient | None = None
) -> EndpointProbeReport:
    client = AsyncOpenAI(
        base_url=endpoint.base_url, api_key=endpoint.api_key or "not-needed", http_client=http_client
    )

    async def _run(owned_http_client: httpx.AsyncClient) -> tuple[bool, str | None]:
        return await _check_tokenize(endpoint, owned_http_client)

    if http_client is not None:
        tokenize_ok, tokenize_error = await _run(http_client)
    else:
        async with httpx.AsyncClient(timeout=10.0) as owned_client:
            tokenize_ok, tokenize_error = await _run(owned_client)

    models_ok, models_error = await _check_models(client)
    levels = [await _check_level(client, endpoint, level) for level in REASONING_LEVELS]
    native_tools_supported, native_tools_error = await _check_native_tools(client, endpoint)

    return EndpointProbeReport(
        models_ok=models_ok,
        models_error=models_error,
        tokenize_ok=tokenize_ok,
        tokenize_error=tokenize_error,
        levels=levels,
        native_tools_supported=native_tools_supported,
        native_tools_error=native_tools_error,
    )
