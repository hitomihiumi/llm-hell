import pytest

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.schemas.search import SearchHit
from app.services.search.answer import (
    _sanitize_answer_text,
    build_prompt,
    extract_citations,
    render_hit,
    select_endpoint,
    synthesize,
)


def endpoint(model_id="glm-4.7", ctx_window=131072, enabled=True) -> ModelEndpoint:
    return ModelEndpoint(
        name=model_id,
        base_url="http://mock-vllm:8000/v1",
        model_id=model_id,
        role="planner",
        ctx_window=ctx_window,
        enabled=enabled,
    )


def hits(n: int, snippet_chars: int = 12) -> list[SearchHit]:
    return [
        SearchHit(
            id=f"s:{i}",
            source="s",
            title=f"Title {i}",
            snippet=f"Body {i} " + "x" * snippet_chars,
            url=f"http://x/{i}",
        )
        for i in range(n)
    ]


def big_hits(n: int) -> list[SearchHit]:
    """Hits large enough that a small context window actually excludes some -
    roughly 350 tokens each at the heuristic's 3.5 chars/token."""
    return hits(n, snippet_chars=1200)


def settings(**overrides) -> Settings:
    return Settings(**overrides)


# --- endpoint selection -----------------------------------------------------

def test_select_endpoint_returns_none_when_nothing_registered():
    """Not an error: search still returns hits, just without an answer."""
    assert select_endpoint([], settings()) is None


def test_select_endpoint_ignores_disabled():
    assert select_endpoint([endpoint(enabled=False)], settings()) is None


def test_select_endpoint_honours_the_pin():
    chosen = select_endpoint(
        [endpoint("a"), endpoint("b")], settings(answer_model_id="b")
    )
    assert chosen.model_id == "b"


def test_select_endpoint_falls_back_when_the_pin_matches_nothing():
    """A stale ANSWER_MODEL_ID should degrade to a working endpoint rather
    than silently disabling every answer."""
    chosen = select_endpoint([endpoint("a")], settings(answer_model_id="gone"))
    assert chosen.model_id == "a"


# --- citation extraction ----------------------------------------------------

def test_citations_resolve_to_the_hits_that_were_in_the_prompt():
    citations, hallucinated = extract_citations("Answer [1] and also [3].", hits(3))
    assert [c.n for c in citations] == [1, 3]
    assert [c.hit_id for c in citations] == ["s:0", "s:2"]
    assert hallucinated == 0


@pytest.mark.parametrize("text,expected_bad", [("see [9]", 1), ("[0]", 1), ("[4][5]", 2)])
def test_out_of_range_citations_are_dropped_and_counted(text, expected_bad):
    """Clamping [9] onto hit 3 would manufacture a citation the model never
    made - so it is discarded, and the count is reported."""
    citations, hallucinated = extract_citations(text, hits(3))
    assert citations == []
    assert hallucinated == expected_bad


def test_repeated_citations_are_deduplicated():
    citations, _ = extract_citations("[1] then [1] again", hits(2))
    assert len(citations) == 1


def test_citations_are_returned_in_numeric_order_regardless_of_appearance():
    citations, _ = extract_citations("first [3] then [1]", hits(3))
    assert [c.n for c in citations] == [1, 3]


@pytest.mark.parametrize("text", ["", None, "no citations here", "[abc]", "[]"])
def test_text_without_usable_citations_yields_none(text):
    citations, hallucinated = extract_citations(text, hits(3))
    assert citations == []
    assert hallucinated == 0


def test_no_citations_can_be_produced_when_there_were_no_hits():
    """The guarantee that makes citations trustworthy: with nothing in the
    prompt, every reference is out of range."""
    citations, hallucinated = extract_citations("Confidently [1] wrong.", [])
    assert citations == []
    assert hallucinated == 1


# --- prompt packing ---------------------------------------------------------

def test_render_hit_numbers_from_one():
    """The number in the prompt is what the model is told to cite, and
    extract_citations reads it as 1-based."""
    assert render_hit(1, hits(1)[0], snippet_chars=100).startswith("[1] source=s")


def test_build_prompt_includes_every_hit_when_the_window_is_large():
    messages, included = build_prompt("q", hits(5), endpoint(), settings=settings())
    assert len(included) == 5
    assert "[5]" in messages[1]["content"]


def test_build_prompt_drops_hits_that_do_not_fit():
    """A small window must yield a shorter prompt, not an over-long one."""
    _, included = build_prompt(
        "q",
        big_hits(50),
        endpoint(ctx_window=4096),
        settings=settings(
            answer_max_output_tokens=512, answer_ctx_reserve_tokens=512, answer_snippet_chars=1200
        ),
    )
    assert 0 < len(included) < 50


def test_build_prompt_always_includes_at_least_one_hit():
    """Even an absurd budget should truncate rather than send an empty
    context and get a hallucinated answer."""
    _, included = build_prompt(
        "q",
        big_hits(5),
        endpoint(ctx_window=1100),
        settings=settings(
            answer_max_output_tokens=512, answer_ctx_reserve_tokens=512, answer_snippet_chars=1200
        ),
    )
    assert len(included) >= 1


def test_build_prompt_with_no_hits_says_so_explicitly():
    messages, included = build_prompt("q", [], endpoint(), settings=settings())
    assert included == []
    assert "no results" in messages[1]["content"]


def test_citation_indices_refer_to_prompt_position_not_search_position():
    """When packing drops hits, [n] must still mean "the nth block in the
    prompt" - otherwise every citation after a dropped hit points at the
    wrong document."""
    all_hits = big_hits(50)
    _, included = build_prompt(
        "q",
        all_hits,
        endpoint(ctx_window=4096),
        settings=settings(
            answer_max_output_tokens=512, answer_ctx_reserve_tokens=512, answer_snippet_chars=1200
        ),
    )
    assert len(included) < len(all_hits), "expected packing to drop some hits"
    citations, _ = extract_citations("[1]", included)
    assert citations[0].hit_id == included[0].id


# --- synthesis --------------------------------------------------------------

async def test_synthesize_without_an_endpoint_reports_it_without_raising():
    result = await synthesize("q", hits(2), None, settings=settings(), http_client=None)
    assert result.error
    assert result.text == ""


async def test_synthesize_against_the_mock_endpoint(mock_vllm_http_client):
    async with mock_vllm_http_client as client:
        result = await synthesize(
            "What is the deployment procedure?",
            hits(3),
            endpoint(),
            settings=settings(),
            http_client=client,
        )
    assert result.error is None
    assert result.model == "glm-4.7"
    assert result.hits_used == 3
    assert isinstance(result.text, str)


def test_latex_symbols_are_sanitized_to_unicode():
    raw = "Diameter is $\\varnothing 60,7$ mm, torque is $\\pm 0.5$ Nm, angle is $30^\\circ$."
    assert _sanitize_answer_text(raw) == "Diameter is ⌀ 60,7 mm, torque is ± 0.5 Nm, angle is 30°."


def test_unknown_latex_commands_are_left_in_place():
    """We only rewrite symbols we recognise; anything else stays readable."""
    assert _sanitize_answer_text("Value is $\\foo 42$") == "Value is \\foo 42"
