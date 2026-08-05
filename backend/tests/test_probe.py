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

    by_level = {lvl.level: lvl for lvl in report.levels}
    assert set(by_level) == {"off", "low", "medium", "high"}
    # "off" maps to reasoning_effort=none, so the mock returns no reasoning;
    # the others do. That difference is exactly what makes this probe useful.
    assert by_level["off"].ok and not by_level["off"].reasoning_content_present
    assert by_level["medium"].ok and by_level["medium"].reasoning_content_present
    assert by_level["high"].ok and by_level["high"].reasoning_content_present
    assert not any(lvl.inline_tags_present for lvl in report.levels)


@pytest.mark.asyncio
async def test_check_endpoint_reports_inline_tags_when_no_native_field(mock_vllm_http_client) -> None:
    report = await check_endpoint(_endpoint("glm-4.7-inline-think"), http_client=mock_vllm_http_client)

    # Checked on a level that actually requests reasoning - "off" would
    # produce no output to find tags in either way.
    by_level = {lvl.level: lvl for lvl in report.levels}
    assert by_level["high"].inline_tags_present
    assert not by_level["high"].reasoning_content_present


@pytest.mark.asyncio
async def test_check_endpoint_reports_no_native_tool_support(mock_vllm_http_client) -> None:
    report = await check_endpoint(_endpoint("glm-4.7-notools"), http_client=mock_vllm_http_client)
    assert not report.native_tools_supported
