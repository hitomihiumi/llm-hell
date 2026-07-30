import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.run import Compaction
from app.services.context.compactor import (
    compact_if_needed,
    persist_compaction,
    should_compact,
    summarize_history,
    truncate_tool_output,
)
from app.services.context.counter import ContextUsage


def _endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id="glm-4.7-noreasoning",
        role="planner",
        ctx_window=1000,
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )


def test_truncate_tool_output_leaves_short_text_untouched() -> None:
    text = "\n".join(f"line {i}" for i in range(10))
    assert truncate_tool_output(text, max_lines=200) == text


def test_truncate_tool_output_truncates_long_text_with_marker() -> None:
    text = "\n".join(f"line {i}" for i in range(500))
    result = truncate_tool_output(text, max_lines=100)

    lines = result.splitlines()
    assert "line 0" in lines[0]
    assert "line 499" in lines[-1]
    assert any("omitted" in line for line in lines)
    assert len(lines) == 101  # 50 head + marker + 50 tail


@pytest.mark.parametrize(
    "total,ctx_window,threshold,expected",
    [
        (750, 1000, 0.75, True),
        (749, 1000, 0.75, False),
        (1000, 1000, 0.75, True),
    ],
)
def test_should_compact_threshold_boundary(total, ctx_window, threshold, expected) -> None:
    usage = ContextUsage(segments={"history": total}, ctx_window=ctx_window)
    assert should_compact(usage, threshold) is expected


@pytest.mark.asyncio
async def test_summarize_history_returns_model_output(mock_vllm_http_client) -> None:
    history = [
        {"role": "user", "content": "fix the failing test"},
        {"role": "assistant", "content": "renamed the helper function"},
    ]
    summary = await summarize_history(history, _endpoint(), mock_vllm_http_client)
    assert summary  # mock server's canned content, but must be non-empty
    assert "mock completion" in summary


@pytest.mark.asyncio
async def test_compact_if_needed_returns_none_below_threshold(mock_vllm_http_client) -> None:
    usage = ContextUsage(segments={"history": 100}, ctx_window=1000)
    result = await compact_if_needed(
        usage=usage,
        threshold=0.75,
        history=[{"role": "user", "content": "hi"}],
        existing_summary=None,
        planner_endpoint=_endpoint(),
        http_client=mock_vllm_http_client,
    )
    assert result is None


@pytest.mark.asyncio
async def test_compact_if_needed_splits_and_summarizes_older_history(mock_vllm_http_client) -> None:
    usage = ContextUsage(segments={"history": 900}, ctx_window=1000)
    history = [{"role": "user", "content": f"turn {i}"} for i in range(10)]

    result = await compact_if_needed(
        usage=usage,
        threshold=0.75,
        history=history,
        existing_summary=None,
        planner_endpoint=_endpoint(),
        http_client=mock_vllm_http_client,
        history_fraction_to_summarize=0.4,
    )

    assert result is not None
    assert result.trigger == "threshold"
    assert result.remaining_history == history[4:]
    assert result.summary


@pytest.mark.asyncio
async def test_compact_if_needed_appends_to_existing_summary(mock_vllm_http_client) -> None:
    usage = ContextUsage(segments={"history": 900}, ctx_window=1000)
    history = [{"role": "user", "content": f"turn {i}"} for i in range(10)]

    result = await compact_if_needed(
        usage=usage,
        threshold=0.75,
        history=history,
        existing_summary="Previously: set up the project.",
        planner_endpoint=_endpoint(),
        http_client=mock_vllm_http_client,
    )

    assert result is not None
    assert "Previously: set up the project." in result.summary


@pytest.mark.asyncio
async def test_compact_if_needed_returns_none_when_history_too_short_to_split(mock_vllm_http_client) -> None:
    usage = ContextUsage(segments={"history": 900}, ctx_window=1000)
    result = await compact_if_needed(
        usage=usage,
        threshold=0.75,
        history=[],
        existing_summary=None,
        planner_endpoint=_endpoint(),
        http_client=mock_vllm_http_client,
    )
    assert result is None


@pytest.mark.asyncio
async def test_persist_compaction_writes_a_row(test_db_engine) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        await persist_compaction(
            session,
            run_id="run-1",
            trigger="threshold",
            tokens_before=900,
            tokens_after=300,
            summary="Collapsed the first four turns.",
        )
        await session.commit()

        row = (await session.execute(select(Compaction).where(Compaction.run_id == "run-1"))).scalar_one()
        assert row.tokens_before == 900
        assert row.tokens_after == 300
        assert row.trigger == "threshold"
