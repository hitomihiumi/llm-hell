import pytest

from app.services.search.text2sql import (
    FallbackTable,
    SqlRejected,
    build_fallback_query,
    escape_literal,
    mask_string_literals,
    parse_generated,
    parse_wrapped_rows,
    strip_sql_comments,
    validate_sql,
    wrap_for_json,
)

TABLES = ["articles", "tickets"]


# --- validate_sql: what must be refused ------------------------------------

@pytest.mark.parametrize(
    "sql,reason",
    [
        ("", "empty"),
        ("   ", "blank"),
        ("DELETE FROM articles", "not a SELECT"),
        ("UPDATE articles SET title = 'x'", "not a SELECT"),
        ("INSERT INTO articles (title) VALUES ('x')", "not a SELECT"),
        ("DROP TABLE articles", "not a SELECT"),
        ("TRUNCATE articles", "not a SELECT"),
        ("GRANT SELECT ON articles TO kb_ro", "not a SELECT"),
        ("SELECT 1; DROP TABLE articles", "two statements"),
        ("SELECT 1; SELECT 2", "two statements"),
        ("SELECT pg_sleep(10)", "forbidden function"),
        ("SELECT pg_read_file('/etc/passwd')", "forbidden function"),
        ("SELECT * FROM dblink('...', 'select 1') AS t(x int)", "forbidden function"),
        ("SELECT * FROM users", "table not whitelisted"),
        ("SELECT * FROM pg_catalog.pg_authid", "table not whitelisted"),
        ("SELECT a.* FROM articles a JOIN users u ON true", "join to a non-whitelisted table"),
        # The classic: hide a second statement behind a line comment.
        ("SELECT 1 -- \n; DROP TABLE articles", "comment-hidden statement"),
        ("SELECT 1 /* */ ; DELETE FROM articles", "comment-hidden statement"),
    ],
)
def test_validate_sql_rejects(sql, reason):
    with pytest.raises(SqlRejected):
        validate_sql(sql, allowed_tables=TABLES)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, title FROM articles LIMIT 10",
        "select id from tickets where status = 'open' limit 5",
        "SELECT id, title FROM articles WHERE body ILIKE '%search%' ORDER BY updated_at DESC LIMIT 20",
        "WITH recent AS (SELECT * FROM articles LIMIT 10) SELECT * FROM recent LIMIT 5",
        "SELECT a.id, t.id FROM articles a JOIN tickets t ON a.id = t.id LIMIT 3",
    ],
)
def test_validate_sql_accepts_read_only_queries(sql):
    assert validate_sql(sql, allowed_tables=TABLES)


def test_column_named_like_a_keyword_is_not_rejected():
    """Word-boundary matching matters: `updated_at` contains "update" and a
    naive substring check would refuse every ordered query."""
    assert validate_sql(
        "SELECT id, updated_at FROM articles ORDER BY updated_at DESC LIMIT 5",
        allowed_tables=TABLES,
    )


def test_missing_limit_is_added():
    assert "LIMIT 50" in validate_sql("SELECT id FROM articles", allowed_tables=TABLES)


def test_existing_limit_is_preserved():
    assert validate_sql("SELECT id FROM articles LIMIT 7", allowed_tables=TABLES).endswith("LIMIT 7")


def test_trailing_semicolon_is_tolerated():
    assert validate_sql("SELECT id FROM articles LIMIT 3;", allowed_tables=TABLES)


def test_empty_whitelist_skips_the_table_check():
    """An unconfigured deployment should not silently refuse everything -
    the read-only role is still the real boundary."""
    assert validate_sql("SELECT id FROM whatever LIMIT 1", allowed_tables=[])


def test_strip_sql_comments():
    assert ";" in strip_sql_comments("SELECT 1 -- hide\n; DROP TABLE x")
    assert "DROP" not in strip_sql_comments("SELECT 1 /* DROP TABLE x */")


def test_mask_string_literals_blanks_content_but_keeps_quotes():
    assert mask_string_literals("SELECT 'a;b' FROM t") == "SELECT '   ' FROM t"


@pytest.mark.parametrize(
    "term",
    ["semi;colon", "double--dash", "DROP TABLE articles", "/* block */", "O''Brien"],
)
def test_search_terms_containing_sql_syntax_are_not_rejected(term):
    """Searching the knowledge base for "DROP TABLE" is an ordinary thing to
    do. Rejecting it adds no safety - the term is inside a string literal -
    and breaks a real query."""
    sql = f"SELECT id, title FROM articles WHERE title ILIKE '%{term}%' LIMIT 5"
    assert validate_sql(sql, allowed_tables=TABLES) == sql


def test_semicolon_outside_a_literal_is_still_rejected():
    with pytest.raises(SqlRejected):
        validate_sql("SELECT id FROM articles WHERE title = 'x'; DROP TABLE articles", allowed_tables=TABLES)


def test_cte_name_is_not_mistaken_for_a_table():
    sql = "WITH recent AS (SELECT * FROM articles LIMIT 10) SELECT * FROM recent LIMIT 5"
    assert validate_sql(sql, allowed_tables=TABLES) == sql


def test_cte_cannot_be_used_to_reach_a_non_whitelisted_table():
    with pytest.raises(SqlRejected):
        validate_sql(
            "WITH leak AS (SELECT * FROM users LIMIT 10) SELECT * FROM leak LIMIT 5",
            allowed_tables=TABLES,
        )


# --- the json_agg wrapper ---------------------------------------------------

def test_wrap_for_json_shape():
    assert wrap_for_json("SELECT id FROM articles") == (
        "SELECT json_agg(t)::text AS payload FROM (SELECT id FROM articles) t"
    )


@pytest.mark.parametrize(
    "payload,expected",
    [
        # The real shape, captured from a live postgres-mcp.
        ([{"payload": '[{"id": 1, "title": "x"}]'}], [{"id": 1, "title": "x"}]),
        # json_agg over zero rows is SQL NULL, not [].
        ([{"payload": None}], []),
        # Defensive: every one of these must yield [] rather than raise.
        ([], []),
        (None, []),
        ({}, []),
        ("not a list", []),
        ([None], []),
        (["string instead of dict"], []),
        ([{"wrong_key": "x"}], []),
        ([{"payload": "not json at all"}], []),
        ([{"payload": '{"not": "a list"}'}], []),
        # Already-decoded list, in case a future server populates
        # structuredContent properly.
        ([{"payload": [{"id": 2}]}], [{"id": 2}]),
        # Non-dict entries inside the array are dropped, not fatal.
        ([{"payload": '[{"id": 1}, "junk", 3]'}], [{"id": 1}]),
    ],
)
def test_parse_wrapped_rows_is_defensive(payload, expected):
    assert parse_wrapped_rows(payload) == expected


# --- parsing the model's reply ----------------------------------------------

GOOD = (
    '{"sql": "SELECT id, title, body FROM articles WHERE body ILIKE \'%x%\' LIMIT 5", '
    '"table": "articles", "id_column": "id", "title_column": "title", "snippet_column": "body"}'
)


def test_parse_generated_happy_path():
    generated = parse_generated(GOOD, allowed_tables=TABLES)
    assert generated.table == "articles"
    assert generated.mode == "llm"
    assert "SELECT" in generated.sql


def test_parse_generated_tolerates_a_fenced_code_block():
    assert parse_generated(f"Sure!\n```json\n{GOOD}\n```\nHope that helps.", allowed_tables=TABLES)


def test_parse_generated_tolerates_surrounding_prose():
    assert parse_generated(f"Here you go: {GOOD} -- let me know", allowed_tables=TABLES)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "I'm sorry, I can't do that.",
        "{not json",
        '["a list, not an object"]',
        '{"sql": "SELECT 1 LIMIT 1"}',  # missing the column mapping
        '{"table": "articles", "id_column": "id", "title_column": "t", "snippet_column": "b"}',  # no sql
    ],
)
def test_parse_generated_rejects_bad_replies(content):
    with pytest.raises(SqlRejected):
        parse_generated(content, allowed_tables=TABLES)


def test_parse_generated_rejects_sql_that_fails_validation():
    """A model told to write a SELECT is not a control - the validator still
    runs on whatever it produced."""
    payload = (
        '{"sql": "DELETE FROM articles", "table": "articles", "id_column": "id", '
        '"title_column": "title", "snippet_column": "body"}'
    )
    with pytest.raises(SqlRejected):
        parse_generated(payload, allowed_tables=TABLES)


def test_parse_generated_rejects_a_table_outside_the_whitelist():
    payload = (
        '{"sql": "SELECT id FROM users LIMIT 1", "table": "users", "id_column": "id", '
        '"title_column": "username", "snippet_column": "username"}'
    )
    with pytest.raises(SqlRejected):
        parse_generated(payload, allowed_tables=TABLES)


# --- the deterministic fallback ---------------------------------------------

def test_fallback_query_is_valid_and_searches_both_columns():
    table = FallbackTable(table="articles", timestamp_column="updated_at")
    generated = build_fallback_query("deploy", table, limit=10)

    assert generated.mode == "fallback"
    assert "title ILIKE '%deploy%'" in generated.sql
    assert "body ILIKE '%deploy%'" in generated.sql
    # Whatever it builds must survive the same validator the model's output does.
    assert validate_sql(generated.sql, allowed_tables=["articles"])


def test_fallback_escapes_quotes_in_the_search_term():
    generated = build_fallback_query("O'Brien", FallbackTable(table="articles"), limit=5)
    assert "O''Brien" in generated.sql
    assert validate_sql(generated.sql, allowed_tables=["articles"])


def test_fallback_with_a_sql_injection_attempt_stays_one_statement():
    generated = build_fallback_query("'; DROP TABLE articles; --", FallbackTable(table="articles"), limit=5)
    # The quote is escaped, so the payload stays inside the string literal
    # and never becomes syntax.
    assert validate_sql(generated.sql, allowed_tables=["articles"])


def test_escape_literal():
    assert escape_literal("it's") == "it''s"
