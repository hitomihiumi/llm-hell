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

# A separate counter, not a fourth `kind` on LLM_TOKENS_TOTAL: cached tokens
# are a *subset* of prompt tokens (the upstream's own `prompt_tokens` already
# includes them), not a fourth disjoint category. Folding them into the same
# counter under their own kind would double-count anything that sums across
# kind, e.g. the "total tokens" panel. This is meant to be read alongside
# `llm_tokens_total{kind="prompt"}`, as a fraction of it - the cache hit rate.
LLM_CACHED_TOKENS_TOTAL = Counter(
    "llm_cached_tokens_total",
    "Prompt tokens served from the provider's own cache - a subset of "
    'llm_tokens_total{kind="prompt"}, not additional to it.',
    ["model", "role"],
)

# Same values as LLM_TOKENS_TOTAL, as a Histogram rather than a Counter: a
# running total answers "how many tokens so far", never "what does one
# request typically cost" - for that, PromQL needs a distribution to run
# histogram_quantile() over. Token counts here span three orders of
# magnitude (a one-line answer to a context near the model's full window),
# so the buckets are powers of two rather than the linear-ish spacing the
# time-based histograms above use.
_TOKEN_BUCKETS = (
    16,
    64,
    256,
    1024,
    4096,
    8192,
    16384,
    32768,
    65536,
    131072,
    262144,
    524288,
    float("inf"),
)

LLM_TOKENS_PER_REQUEST = Histogram(
    "llm_tokens_per_request",
    "Tokens in a single request, by kind - for percentiles (median, p95) rather than totals.",
    ["model", "role", "kind"],  # kind: prompt | completion | reasoning | cached
    buckets=_TOKEN_BUCKETS,
)

# Mirrors cost_usd in `llm_requests`, which was Postgres-only until now - a
# $/hour burn-rate panel and an error-budget-style alert both want this as a
# live counter rather than a value queried out of Postgres on a timer.
LLM_COST_USD_TOTAL = Counter(
    "llm_cost_usd_total",
    "Total cost of proxied requests, in USD, from each endpoint's configured price.",
    ["model", "role"],
)

LLM_COST_USD_PER_REQUEST = Histogram(
    "llm_cost_usd_per_request",
    "Cost of a single request, in USD - for the median/p95 cost panel.",
    ["model", "role"],
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, float("inf")),
)

LLM_ERRORS_TOTAL = Counter(
    "llm_errors_total",
    "Total chat completion requests that raised an error.",
    ["model", "role", "type"],
)
