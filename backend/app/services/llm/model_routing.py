"""Maps between a single vLLM endpoint (one physical model, one
`reasoning_profile`) and the several OpenAI "model" ids opencode sees for
it - one per configured reasoning level. opencode has no concept of a
GLM-style reasoning knob; it only picks a model. So one endpoint is
published under several model ids ("glm-4.7", "glm-4.7-high", ...) and a
tester picks a reasoning level by picking a model, the same way they'd
pick between any other two models in their provider config.
"""

from app.models.endpoint import ModelEndpoint


def published_model_ids(endpoint: ModelEndpoint) -> list[tuple[str, str]]:
    """Returns [(published_model_id, reasoning_level), ...] for one
    endpoint. The "off" level (if configured) publishes under the bare
    `model_id`; every other level gets a "-{level}" suffix."""
    levels = (endpoint.reasoning_profile or {}).get("levels") or {}
    if not levels:
        return [(endpoint.model_id, "off")]

    published: list[tuple[str, str]] = []
    for level in levels:
        model_id = endpoint.model_id if level == "off" else f"{endpoint.model_id}-{level}"
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
