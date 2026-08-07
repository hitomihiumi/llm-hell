"""Maps a vLLM endpoint's `model_id` to the OpenAI "model" id opencode sees.

One endpoint publishes exactly one model id. There is no per-level suffix.

That is a deliberate reversal: this used to publish "deepseek-v4-flash",
"-low", "-medium", "-high" so a tester could pick a reasoning level by
picking a model, because opencode was thought to have no notion of one. It
does - a built-in effort selector (Default/Low/Medium/High/Max) that sends
`reasoning_effort` on the request. Publishing four ids duplicated that
control, and the proxy's own override of `reasoning_effort` actively broke
it: whichever level the selector chose was replaced by the one implied by
the model id.

The level now comes from the request, and is recorded from there too - see
`resolve_reasoning_level` in app.api.openai_proxy.
"""

from app.models.endpoint import ModelEndpoint

# What gets recorded when a request carries no reasoning_effort at all,
# matching the label opencode's selector shows for that case.
DEFAULT_LEVEL = "default"


def published_model_ids(endpoint: ModelEndpoint) -> list[tuple[str, str]]:
    """Returns [(published_model_id, level)] - always a single entry.

    The tuple shape is kept because callers still want a level to record
    against, and because it leaves room to reintroduce per-model variants
    that are not about reasoning.
    """
    return [(endpoint.model_id, DEFAULT_LEVEL)]


def resolve_model(endpoints: list[ModelEndpoint], requested_model: str) -> tuple[ModelEndpoint, str] | None:
    """Finds which endpoint a client-requested `model` string refers to."""
    for endpoint in endpoints:
        if endpoint.model_id == requested_model:
            return endpoint, DEFAULT_LEVEL
    return None
