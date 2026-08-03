"""Prometheus metrics for the proxy. A single process (`api`) now serves
everything - there's no separate worker anymore, since the agent loop
runs inside opencode on the tester's machine, out of this service's view.

Label cardinality is kept low on purpose (`model`/`role`/`reasoning_level`,
never `session_id` or `user_id`) since these are process-wide
counters/histograms, not a per-request audit log - that detail lives in
Postgres via `llm_requests` (see `services.stats.recorder`).
"""

from prometheus_client import Counter, Histogram

# Explicit buckets on every histogram below, never the prometheus_client
# defaults. Those defaults (.005 ... 7.5, 10, +Inf) are sized for
# sub-second HTTP latency, and `histogram_quantile()` cannot interpolate
# past the highest *finite* bucket - once a quantile lands in +Inf it
# returns that last boundary verbatim. With the defaults every one of
# these three metrics silently pinned itself to a flat 10 in Grafana
# (10 tok/s, 10s duration, 10s TTFT) no matter the real value, because
# LLM traffic sits almost entirely above 10 on all three.

LLM_TTFT_SECONDS = Histogram(
    "llm_ttft_seconds",
    "Time to first token (content or reasoning) after a chat completion request is sent.",
    ["model", "role", "reasoning_level"],
    # Upper end is deliberately generous: prefill on a near-full 200k
    # context is minutes, not seconds, and clamping that back to 10s is
    # exactly the failure mode being fixed here.
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, float("inf")),
)

LLM_REQUEST_DURATION_SECONDS = Histogram(
    "llm_request_duration_seconds",
    "Total duration of a proxied chat completion request, start to finish.",
    ["model", "role", "reasoning_level"],
    # A long agentic completion runs for minutes; the top bucket is above
    # the proxy's own 300s upstream read timeout so a request that dies on
    # that timeout still lands in a finite bucket rather than +Inf.
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, float("inf")),
)

LLM_OUTPUT_TPS = Histogram(
    "llm_output_tps",
    "Completion tokens per second for a chat completion request.",
    ["model", "role", "reasoning_level"],
    # Tokens/sec, not seconds - a different scale entirely from the two
    # above. GLM 4.7 Flash on a single card runs well past 100 tok/s.
    buckets=(1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, float("inf")),
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
