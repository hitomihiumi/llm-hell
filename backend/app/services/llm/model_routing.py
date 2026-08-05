"""Maps between a single vLLM endpoint (one physical model, one
`reasoning_profile`) and the several OpenAI "model" ids opencode sees for
it - one per configured reasoning level.

opencode has no concept of a reasoning-effort knob; it only picks a model.
So one endpoint is published under several model ids ("deepseek-v4-flash",
"deepseek-v4-flash-high", ...) and a tester chooses a reasoning level by
choosing a model, exactly the way they'd choose between any two models in
their provider config.

An endpoint with no `levels` in its profile publishes exactly one id, the
bare `model_id`, and every request against it goes upstream with no
reasoning field - the escape hatch for a model that misbehaves when asked
for an effort level (see DEFAULT_REASONING_PROFILE).
"""

from app.models.endpoint import ModelEndpoint

# Published under the bare model_id rather than a "-off" suffix: it is the
# default a tester gets when they just pick the model by name.
BASE_LEVEL = "off"


def published_model_ids(endpoint: ModelEndpoint) -> list[tuple[str, str]]:
    """Returns [(published_model_id, reasoning_level), ...] for one
    endpoint. The "off" level (if configured) publishes under the bare
    `model_id`; every other level gets a "-{level}" suffix."""
    levels = (endpoint.reasoning_profile or {}).get("levels") or {}
    if not levels:
        return [(endpoint.model_id, BASE_LEVEL)]

    published: list[tuple[str, str]] = []
    for level in levels:
        model_id = endpoint.model_id if level == BASE_LEVEL else f"{endpoint.model_id}-{level}"
        published.append((model_id, level))
    return published


def resolve_model(endpoints: list[ModelEndpoint], requested_model: str) -> tuple[ModelEndpoint, str] | None:
    """Finds which endpoint + reasoning level a client-requested `model`
    string refers to, across every endpoint's own published ids."""
    for endpoint in endpoints:
        for published_id, level in published_model_ids(endpoint):
            if published_id == requested_model:
                return endpoint, level
    return None
