"""Follow-up turns in the chat layout."""

import pytest

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.schemas.search import ChatTurn, SearchHit
from app.services.search.answer import MAX_HISTORY_TURNS, build_prompt


def endpoint(ctx_window: int = 131072) -> ModelEndpoint:
    return ModelEndpoint(
        name="m",
        base_url="http://mock-vllm:8000/v1",
        model_id="glm-4.7",
        role="planner",
        ctx_window=ctx_window,
    )


def hits(n: int) -> list[SearchHit]:
    return [SearchHit(id=f"s:{i}", source="s", title=f"T{i}", snippet="body") for i in range(n)]


def test_no_history_gives_the_original_two_message_prompt():
    messages, _ = build_prompt("q", hits(2), endpoint(), settings=Settings())
    assert [m["role"] for m in messages] == ["system", "user"]


def test_history_is_inserted_between_system_and_the_current_turn():
    """The results must be the LAST thing the model reads, or a long
    conversation buries them."""
    history = [
        ChatTurn(role="user", content="what is RRF?"),
        ChatTurn(role="assistant", content="Reciprocal rank fusion [1]."),
    ]
    messages, _ = build_prompt(
        "what about the second one?", hits(2), endpoint(), settings=Settings(), history=history
    )

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "what is RRF?"
    assert "Search results:" in messages[-1]["content"]
    assert "what about the second one?" in messages[-1]["content"]


def test_only_the_most_recent_turns_are_carried():
    history = [
        ChatTurn(role="user" if i % 2 == 0 else "assistant", content=f"turn {i}") for i in range(20)
    ]
    messages, _ = build_prompt("q", hits(1), endpoint(), settings=Settings(), history=history)

    carried = [m for m in messages if m["content"].startswith("turn ")]
    assert len(carried) == MAX_HISTORY_TURNS
    # The most recent ones, not the oldest.
    assert carried[-1]["content"] == "turn 19"


def test_the_schema_caps_turn_length():
    """First layer: an over-long turn never reaches the prompt builder."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        ChatTurn(role="user", content="x" * 50_000)


def test_long_turns_are_truncated():
    """Second layer, for a caller that bypasses the schema. Even a turn
    inside the schema's limit is cut before it can eat the hit budget."""
    history = [{"role": "user", "content": "x" * 50_000}]
    messages, _ = build_prompt("q", hits(1), endpoint(), settings=Settings(), history=history)
    carried = next(m for m in messages if m["content"].startswith("x"))
    assert len(carried["content"]) == 1500


def test_history_does_not_squeeze_out_every_hit():
    """The hits are the point of the answer; history is context. A long
    conversation must not leave the model answering from nothing."""
    history = [ChatTurn(role="user", content="y" * 1500) for _ in range(MAX_HISTORY_TURNS)]
    _, included = build_prompt(
        "q", hits(10), endpoint(ctx_window=8192), settings=Settings(), history=history
    )
    assert len(included) >= 1


@pytest.mark.parametrize(
    "history",
    [
        None,
        [],
        [{"role": "user", "content": "dict form works too"}],
        [{"role": "system", "content": "not allowed here"}],
        [{"role": "user", "content": ""}],
        [{"role": "user"}],
        [{"content": "no role"}],
        ["not a turn at all"],
    ],
)
def test_malformed_history_never_breaks_the_prompt(history):
    messages, _ = build_prompt("q", hits(1), endpoint(), settings=Settings(), history=history)
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert "Search results:" in messages[-1]["content"]
    # Anything unusable is dropped rather than passed through.
    assert all(m["role"] in ("system", "user", "assistant") for m in messages)
