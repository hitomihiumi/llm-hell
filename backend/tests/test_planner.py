import pytest

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.services.agent.planner import PlanParseError, generate_plan, parse_plan_response


def _endpoint(model_id: str = "glm-4.7") -> ModelEndpoint:
    return ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id=model_id,
        role="planner",
        ctx_window=32768,
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )


def test_parse_plan_response_from_fenced_block() -> None:
    content = (
        "Sure, here's the plan:\n\n"
        '```plan\n[{"id": "step-1", "title": "Do the thing", "intent": "because", '
        '"files": ["a.py"], "done_when": "tests pass"}]\n```'
    )
    steps = parse_plan_response(content)
    assert len(steps) == 1
    assert steps[0].id == "step-1"
    assert steps[0].title == "Do the thing"
    assert steps[0].files == ["a.py"]


def test_parse_plan_response_bare_json_array() -> None:
    content = '[{"title": "Only a title"}]'
    steps = parse_plan_response(content)
    assert steps[0].id == "step-1"  # auto-generated when omitted
    assert steps[0].title == "Only a title"
    assert steps[0].files == []
    assert steps[0].done_when == ""


def test_parse_plan_response_multi_step() -> None:
    content = '```plan\n[{"title": "A"}, {"title": "B"}, {"title": "C"}]\n```'
    steps = parse_plan_response(content)
    assert [s.title for s in steps] == ["A", "B", "C"]
    assert [s.id for s in steps] == ["step-1", "step-2", "step-3"]


@pytest.mark.parametrize(
    "content",
    ["", "not json at all", "{}", "[]", '[{"no_title": "x"}]', "[1, 2, 3]"],
)
def test_parse_plan_response_rejects_invalid_input(content: str) -> None:
    with pytest.raises(PlanParseError):
        parse_plan_response(content)


@pytest.mark.asyncio
async def test_generate_plan_against_mock_server(mock_vllm_http_client) -> None:
    generated = await generate_plan(
        task_text="fix the failing test",
        all_files=[],
        endpoint=_endpoint(),
        reasoning_level="medium",
        http_client=mock_vllm_http_client,
    )

    assert len(generated.steps) == 2
    assert generated.steps[0].title == "Investigate the failing behaviour"
    assert generated.steps[1].done_when == "The tests pass."
    assert "Analyzing" in generated.reasoning


