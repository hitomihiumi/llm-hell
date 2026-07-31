"""Maps a vLLM endpoint's `model_id` to the OpenAI "model" id opencode
sees for it.

This used to publish several ids per endpoint - one per reasoning level,
e.g. "glm-4.7", "glm-4.7-high" - so a tester could pick a
`reasoning_effort` value by picking a model. Removed after GLM-4.7 (this
project's actual target model) was found to emit corrupted/looping output
whenever `reasoning_effort` was set to anything at all upstream,
regardless of value. One endpoint now always publishes exactly one model
id; the second element of each tuple stays "off" only because
`record_request`'s `reasoning_level` column still expects a value.
"""

from app.models.endpoint import ModelEndpoint


def published_model_ids(endpoint: ModelEndpoint) -> list[tuple[str, str]]:
    return [(endpoint.model_id, "off")]


def resolve_model(endpoints: list[ModelEndpoint], requested_model: str) -> tuple[ModelEndpoint, str] | None:
    """Finds which endpoint a client-requested `model` string refers to."""
    for endpoint in endpoints:
        if endpoint.model_id == requested_model:
            return endpoint, "off"
    return None
