import pytest

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.services.llm.probe import check_endpoint


def _endpoint(model_id: str) -> ModelEndpoint:
    return ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id=model_id,
        role="planner",
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )


@pytest.mark.asyncio
async def test_check_endpoint_reports_field_mode_reasoning_and_native_tools(mock_vllm_http_client) -> None:
    report = await check_endpoint(_endpoint("glm-4.7"), http_client=mock_vllm_http_client)

    assert report.models_ok
    assert report.tokenize_ok
    assert report.native_tools_supported

    assert len(report.levels) == 1
    probed = report.levels[0]
    assert probed.level == "off"
    assert probed.ok and probed.reasoning_content_present
    assert not probed.inline_tags_present


@pytest.mark.asyncio
async def test_check_endpoint_reports_inline_tags_when_no_native_field(mock_vllm_http_client) -> None:
    report = await check_endpoint(_endpoint("glm-4.7-inline-think"), http_client=mock_vllm_http_client)

    probed = report.levels[0]
    assert probed.inline_tags_present
    assert not probed.reasoning_content_present


@pytest.mark.asyncio
async def test_check_endpoint_reports_no_native_tool_support(mock_vllm_http_client) -> None:
    report = await check_endpoint(_endpoint("glm-4.7-notools"), http_client=mock_vllm_http_client)
    assert not report.native_tools_supported
