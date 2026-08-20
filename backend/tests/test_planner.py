"""Letting the model write the search queries.

The search was literal and context-blind, and the same chat answered the same
question differently depending on how it was phrased. "чи присутній тут
гіроскоп" found nothing - `тут` lives in the previous turn, and `гіроскоп`
appears in the datasheet as `IMU: MPU6000`. "чи присутній гіроскоп на платі
F722" worked, because `F722` happened to be a literal match.

Against the live stack the planner now turns the first form into
`["чи присутній тут гіроскоп", "F722 IMU", "F722 gyro", "F722 MPU6000"]` and
the answer is "Так, гіроскоп присутній; на платі встановлено IMU MPU6000 [1]".
"""

import pytest

from app.core.config import Settings
from app.services.search import planner


def test_a_plain_object_is_read():
    assert planner.parse_queries('{"queries": ["a", "b"]}', limit=3) == ["a", "b"]


def test_a_fenced_block_is_read_too():
    """A model told to emit bare JSON will sometimes wrap it anyway, and
    throwing away a good plan over punctuation would be silly."""
    fenced = '```json\n{"queries": ["F722 IMU"]}\n```'

    assert planner.parse_queries(fenced, limit=3) == ["F722 IMU"]


def test_a_bare_list_is_accepted():
    assert planner.parse_queries('["F722 IMU", "F722 gyro"]', limit=3) == ["F722 IMU", "F722 gyro"]


def test_duplicates_cost_a_whole_fan_out_so_they_are_dropped():
    assert planner.parse_queries('{"queries": ["F722 IMU", "f722 imu"]}', limit=3) == ["F722 IMU"]


def test_the_limit_is_honoured():
    got = planner.parse_queries('{"queries": ["a", "b", "c", "d"]}', limit=2)

    assert got == ["a", "b"]


def test_anything_unparseable_plans_nothing():
    """The caller falls back to the question as typed, so nonsense here costs
    the improvement and never the search."""
    assert planner.parse_queries("I think you should search for gyros!", limit=3) == []
    assert planner.parse_queries("", limit=3) == []
    assert planner.parse_queries('{"queries": "not a list"}', limit=3) == []


# --- the prompt -------------------------------------------------------------


def test_the_conversation_is_given_to_the_planner():
    """This is the whole point: the answer already had history and the search
    did not, so a follow-up lost its subject."""
    messages = planner.build_prompt(
        "чи присутній тут гіроскоп",
        [
            {"role": "user", "content": "що це за плата F722"},
            {"role": "assistant", "content": "Matek F722-HD"},
        ],
        max_queries=3,
    )

    assert "F722" in messages[1]["content"]
    assert "чи присутній тут гіроскоп" in messages[1]["content"]


def test_a_first_question_says_so_rather_than_showing_an_empty_block():
    messages = planner.build_prompt("what is the recency boost", None, max_queries=3)

    assert "first question" in messages[1]["content"]


def test_the_example_braces_survive_formatting():
    """The system prompt is `.format()`ed for max_queries, and the JSON
    example in it is made of braces - which raised KeyError on the example
    itself until they were doubled."""
    messages = planner.build_prompt("q", None, max_queries=2)

    assert '{"queries"' in messages[0]["content"]
    assert "1 and 2 queries" in messages[0]["content"]


# --- the fallback contract --------------------------------------------------


@pytest.mark.asyncio
async def test_no_endpoint_searches_exactly_what_was_typed():
    got = await planner.plan_queries(
        "gyro?", history=None, endpoint=None, http_client=None, settings=Settings()
    )

    assert got == ["gyro?"]


@pytest.mark.asyncio
async def test_planning_switched_off_searches_exactly_what_was_typed():
    got = await planner.plan_queries(
        "gyro?",
        history=None,
        endpoint=object(),
        http_client=None,
        settings=Settings(search_plan_queries=0),
    )

    assert got == ["gyro?"]


@pytest.mark.asyncio
async def test_a_model_failure_never_prevents_a_search(monkeypatch):
    async def fake_complete(*args, **kwargs):
        raise planner.chat.ChatError("endpoint unreachable")

    monkeypatch.setattr(planner.chat, "complete", fake_complete)

    got = await planner.plan_queries(
        "gyro?", history=None, endpoint=object(), http_client=None, settings=Settings()
    )

    assert got == ["gyro?"]


@pytest.mark.asyncio
async def test_the_question_as_typed_always_leads(monkeypatch):
    """A plan is an addition. On a corpus that happens to share the user's
    vocabulary, what they typed is the best query there is."""

    class Result:
        content = '{"queries": ["F722 IMU", "F722 gyro"]}'

    async def fake_complete(*args, **kwargs):
        return Result()

    monkeypatch.setattr(planner.chat, "complete", fake_complete)

    got = await planner.plan_queries(
        "чи присутній тут гіроскоп",
        history=None,
        endpoint=object(),
        http_client=None,
        settings=Settings(),
    )

    assert got[0] == "чи присутній тут гіроскоп"
    assert "F722 IMU" in got
