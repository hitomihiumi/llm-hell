"""Reasoning: requesting a level upstream, and parsing what comes back.

The whole point of this module is that the real behaviour of a given vLLM
deployment (whether it echoes reasoning as a separate `reasoning_content`
delta field, as inline `<think>...</think>` tags in `content`, or not at
all; and whether it honours a requested effort level at all) is unknown
until probed against the live endpoint. Everything here is driven by the
endpoint's stored `reasoning_profile`, not hardcoded assumptions, so an
admin can correct it from the "check endpoint" flow without a code change.

Requesting a level was removed from this module once, after GLM-4.7 was
found to emit corrupted/looping output whenever `reasoning_effort` was set
to anything at all. It is back for DeepSeek V4, which is a reasoning model
with real effort levels - but the mechanism stays per-endpoint on purpose:
an endpoint whose model misbehaves like GLM did just gets an empty
`levels` map in its profile, which disables the whole thing for that
endpoint without touching code or affecting any other endpoint.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

# Level names as this service uses them - deliberately NOT the literals
# sent upstream. vLLM validates `reasoning_effort` against
# 'none'/'minimal'/'low'/'medium'/'high'/'xhigh'/'max' and rejects anything
# else with a 400, so "off" (which reads better in a model id, and matches
# opencode's own vocabulary) maps to "none" in the profile rather than
# being sent verbatim.
REASONING_LEVELS = ("off", "low", "medium", "high")
ReasoningLevel = Literal["off", "low", "medium", "high"]

ParseMode = Literal["auto", "field", "tags"]


def build_extra_body(reasoning_profile: dict[str, Any], level: str) -> dict[str, Any]:
    """Extra top-level JSON fields to merge into the upstream chat
    completion request for the given reasoning level.

    Returns an empty dict for an unknown level, and for every level when
    the endpoint has no `levels` configured - in both cases the request
    goes upstream with no reasoning field at all, which is what we want:
    the model then does whatever it does by default.
    """
    level_cfg = (reasoning_profile or {}).get("levels", {}).get(level, {})
    return dict(level_cfg.get("extra_body", {}))


@dataclass
class ParsedDelta:
    reasoning_text: str = ""
    content_text: str = ""


class ReasoningStreamParser:
    """Stateful per-response parser that splits streamed deltas into
    reasoning vs. content text, according to a `parse` config of the form
    `{"mode": "auto"|"field"|"tags", "field": str, "tags": [open, close]}`.

    In "auto" mode, the first chunk decides: if it carries a non-empty
    `reasoning_content`-style field, the whole response is treated as
    field-mode. Otherwise every chunk is run through the inline-tag
    state machine, which is a no-op passthrough when no tags ever show up
    - so "auto" safely covers endpoints with no reasoning output too.
    """

    def __init__(self, parse_config: dict[str, Any]):
        self._configured_mode: ParseMode = parse_config.get("mode", "auto")
        self._field = parse_config.get("field", "reasoning_content")
        tags = parse_config.get("tags") or ["<think>", "</think>"]
        self._open_tag, self._close_tag = tags[0], tags[1]

        self._mode: Literal["undetermined", "field", "tags"] = (
            "field" if self._configured_mode == "field" else "tags" if self._configured_mode == "tags" else "undetermined"
        )
        self._buffer = ""
        self._in_think = False

    def feed(self, delta: dict[str, Any]) -> ParsedDelta:
        content_piece = delta.get("content") or ""
        # The configured field first, then the other conventional names:
        # vLLM 0.26.0 streams reasoning in "reasoning", older builds in
        # "reasoning_content", and a profile written against one server
        # would otherwise count zero reasoning tokens on the other.
        reasoning_field_piece = ""
        for candidate in (self._field, "reasoning", "reasoning_content", "reasoning_text"):
            piece = delta.get(candidate)
            if piece:
                reasoning_field_piece = piece
                break

        if self._mode == "undetermined":
            if reasoning_field_piece:
                self._mode = "field"
            elif content_piece:
                self._mode = "tags"
            else:
                # Nothing to decide from yet, e.g. the leading
                # `{"role": "assistant"}` chunk OpenAI-style APIs send first.
                return ParsedDelta()

        if self._mode == "field":
            return ParsedDelta(reasoning_text=reasoning_field_piece, content_text=content_piece)

        return self._feed_tags(content_piece)

    def _feed_tags(self, content_piece: str) -> ParsedDelta:
        if not content_piece:
            return ParsedDelta()

        self._buffer += content_piece
        out_reasoning = ""
        out_content = ""

        while True:
            if not self._in_think:
                idx = self._buffer.find(self._open_tag)
                if idx == -1:
                    safe_len = max(0, len(self._buffer) - (len(self._open_tag) - 1))
                    out_content += self._buffer[:safe_len]
                    self._buffer = self._buffer[safe_len:]
                    break
                out_content += self._buffer[:idx]
                self._buffer = self._buffer[idx + len(self._open_tag) :]
                self._in_think = True
            else:
                idx = self._buffer.find(self._close_tag)
                if idx == -1:
                    safe_len = max(0, len(self._buffer) - (len(self._close_tag) - 1))
                    out_reasoning += self._buffer[:safe_len]
                    self._buffer = self._buffer[safe_len:]
                    break
                out_reasoning += self._buffer[:idx]
                self._buffer = self._buffer[idx + len(self._close_tag) :]
                self._in_think = False

        return ParsedDelta(reasoning_text=out_reasoning, content_text=out_content)

    def flush(self) -> ParsedDelta:
        """Call once the stream ends to drain any buffered tail text."""
        if self._mode == "field" or not self._buffer:
            return ParsedDelta()
        # Malformed/truncated stream: salvage whatever is left as content.
        remainder = self._buffer
        self._buffer = ""
        return ParsedDelta(content_text=remainder)


@dataclass
class ReasoningProbeResult:
    level: str
    reasoning_seen: bool
    reasoning_mode_detected: Literal["field", "tags", "none"]
    sample: str = field(default="")
