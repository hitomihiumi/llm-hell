import pytest

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.services.context.counter import count_segments


def _endpoint(ctx_window: int = 32768) -> ModelEndpoint:
    return ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id="glm-4.7",
        role="planner",
        ctx_window=ctx_window,
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )


@pytest.mark.asyncio
async def test_count_segments_counts_each_text_independently(mock_vllm_http_client) -> None:
    usage = await count_segments(
        {"system": "a" * 40, "repo_map": "b" * 20, "pinned": ""},
        _endpoint(),
        mock_vllm_http_client,
    )

    assert usage.segments["system"] == 10  # mock tokenizer: len // 4
    assert usage.segments["repo_map"] == 5
    assert usage.segments["pinned"] == 0
    assert usage.total == 15
    assert usage.ctx_window == 32768


@pytest.mark.asyncio
async def test_fraction_used_and_to_dict(mock_vllm_http_client) -> None:
    usage = await count_segments({"system": "a" * 8192}, _endpoint(ctx_window=1000), mock_vllm_http_client)

    assert usage.total == 2048
    assert usage.fraction_used == pytest.approx(2.048)

    d = usage.to_dict()
    assert d["total"] == 2048
    assert d["ctx_window"] == 1000
    assert d["reasoning_tokens"] == 0
    assert "segments" in d


def test_fraction_used_zero_window_is_safe() -> None:
    from app.services.context.counter import ContextUsage

    usage = ContextUsage(segments={"system": 10}, ctx_window=0)
    assert usage.fraction_used == 0.0
