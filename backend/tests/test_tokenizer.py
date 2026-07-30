import pytest

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.services.llm.tokenizer import count_tokens, heuristic_token_count, tokenize_root_url


def test_tokenize_root_url_strips_v1_suffix() -> None:
    assert tokenize_root_url("http://host:8000/v1") == "http://host:8000/tokenize"
    assert tokenize_root_url("http://host:8000/v1/") == "http://host:8000/tokenize"


def test_heuristic_token_count_scales_with_length() -> None:
    assert heuristic_token_count("") == 0
    assert heuristic_token_count("a") == 1
    assert heuristic_token_count("a" * 35) == 10


@pytest.mark.asyncio
async def test_count_tokens_uses_endpoint_tokenize_route(mock_vllm_http_client) -> None:
    endpoint = ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id="glm-4.7",
        role="executor",
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )

    count = await count_tokens(endpoint, "hello world this is a test", mock_vllm_http_client)
    assert count == 6  # mock tokenizer: len(text) // 4

    # cached path returns the same value without a second network call
    cached_count = await count_tokens(endpoint, "hello world this is a test", mock_vllm_http_client)
    assert cached_count == count


@pytest.mark.asyncio
async def test_count_tokens_empty_text_is_free() -> None:
    endpoint = ModelEndpoint(
        id="ep-empty",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id="glm-4.7",
        role="executor",
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )
    assert await count_tokens(endpoint, "", None) == 0  # type: ignore[arg-type]
