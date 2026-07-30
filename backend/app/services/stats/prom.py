"""Prometheus metrics for the proxy. A single process (`api`) now serves
everything - there's no separate worker anymore, since the agent loop
runs inside opencode on the tester's machine, out of this service's view.

Label cardinality is kept low on purpose (`model`/`role`/`reasoning_level`,
never `session_id` or `user_id`) since these are process-wide
counters/histograms, not a per-request audit log - that detail lives in
Postgres via `llm_requests` (see `services.stats.recorder`).
"""

from prometheus_client import Counter, Histogram

LLM_TTFT_SECONDS = Histogram(
    "llm_ttft_seconds",
    "Time to first token (content or reasoning) after a chat completion request is sent.",
    ["model", "role", "reasoning_level"],
)

LLM_REQUEST_DURATION_SECONDS = Histogram(
    "llm_request_duration_seconds",
    "Total duration of a proxied chat completion request, start to finish.",
    ["model", "role", "reasoning_level"],
)

LLM_OUTPUT_TPS = Histogram(
    "llm_output_tps",
    "Completion tokens per second for a chat completion request.",
    ["model", "role", "reasoning_level"],
)

LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Total tokens consumed, by kind.",
    ["model", "role", "kind"],  # kind: prompt | completion | reasoning
)

LLM_ERRORS_TOTAL = Counter(
    "llm_errors_total",
    "Total chat completion requests that raised an error.",
    ["model", "role", "type"],
)
