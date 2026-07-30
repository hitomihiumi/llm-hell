"""Per-segment token counting that backs both the assembler's budget
decisions and the UI's context bar (`context_update` SSE event, task 6).
"""

from dataclasses import dataclass, field

import httpx

from app.models.endpoint import ModelEndpoint
from app.services.llm.tokenizer import count_tokens

SEGMENT_NAMES = ("system", "repo_map", "pinned", "retrieved", "summary", "history")


@dataclass
class ContextUsage:
    segments: dict[str, int] = field(default_factory=dict)
    ctx_window: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return sum(self.segments.values())

    @property
    def fraction_used(self) -> float:
        if self.ctx_window <= 0:
            return 0.0
        return self.total / self.ctx_window

    def to_dict(self) -> dict:
        return {
            "segments": dict(self.segments),
            "total": self.total,
            "ctx_window": self.ctx_window,
            "fraction_used": self.fraction_used,
            "reasoning_tokens": self.reasoning_tokens,
        }


async def count_segments(
    texts: dict[str, str],
    endpoint: ModelEndpoint,
    http_client: httpx.AsyncClient,
) -> ContextUsage:
    segments = {name: await count_tokens(endpoint, text, http_client) for name, text in texts.items()}
    return ContextUsage(segments=segments, ctx_window=endpoint.ctx_window)
