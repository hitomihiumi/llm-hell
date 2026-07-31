from app.services.llm.reasoning import ReasoningStreamParser

FIELD_PARSE_CFG = {"mode": "auto", "field": "reasoning_content", "tags": ["<think>", "</think>"]}


def test_field_mode_auto_detected_from_first_chunk() -> None:
    parser = ReasoningStreamParser(FIELD_PARSE_CFG)

    first = parser.feed({"reasoning_content": "thinking... "})
    assert first.reasoning_text == "thinking... "
    assert first.content_text == ""

    second = parser.feed({"content": "answer"})
    assert second.content_text == "answer"
    assert second.reasoning_text == ""


def test_tags_mode_auto_detected_when_no_field_present() -> None:
    parser = ReasoningStreamParser(FIELD_PARSE_CFG)

    chunks = ["<think>", "reason", "ing here", "</think>", "the answer"]
    reasoning = ""
    content = ""
    for chunk in chunks:
        parsed = parser.feed({"content": chunk})
        reasoning += parsed.reasoning_text
        content += parsed.content_text
    tail = parser.flush()
    content += tail.content_text

    assert reasoning == "reasoning here"
    assert content == "the answer"


def test_tags_mode_handles_tag_split_across_chunk_boundary() -> None:
    parser = ReasoningStreamParser(FIELD_PARSE_CFG)

    chunks = ["<thi", "nk>reasoning</th", "ink>answer"]
    reasoning = ""
    content = ""
    for chunk in chunks:
        parsed = parser.feed({"content": chunk})
        reasoning += parsed.reasoning_text
        content += parsed.content_text
    tail = parser.flush()
    content += tail.content_text

    assert reasoning == "reasoning"
    assert content == "answer"


def test_plain_content_passes_through_when_no_tags_or_field_ever_appear() -> None:
    parser = ReasoningStreamParser(FIELD_PARSE_CFG)

    parsed1 = parser.feed({"content": "just a "})
    parsed2 = parser.feed({"content": "normal reply"})
    tail = parser.flush()

    assert parsed1.reasoning_text == ""
    assert parsed2.reasoning_text == ""
    assert parsed1.content_text + parsed2.content_text + tail.content_text == "just a normal reply"


def test_forced_tags_mode_ignores_reasoning_field() -> None:
    cfg = {"mode": "tags", "field": "reasoning_content", "tags": ["<think>", "</think>"]}
    parser = ReasoningStreamParser(cfg)

    parsed = parser.feed({"reasoning_content": "should be ignored", "content": "<think>x</think>y"})
    tail = parser.flush()
    assert parsed.reasoning_text == "x"
    assert parsed.content_text + tail.content_text == "y"


def test_forced_field_mode_ignores_inline_tags() -> None:
    cfg = {"mode": "field", "field": "reasoning_content", "tags": ["<think>", "</think>"]}
    parser = ReasoningStreamParser(cfg)

    parsed = parser.feed({"content": "<think>literal text, not parsed</think>"})
    assert parsed.reasoning_text == ""
    assert parsed.content_text == "<think>literal text, not parsed</think>"
