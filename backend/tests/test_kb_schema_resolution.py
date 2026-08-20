"""Resolving fallback columns against the real schema.

The connector used to key this on the table name (`updated_at` for
`articles`, `created_at` for everything else), which is correct only for the
two tables that existed when it was written. The seeded corpus now has four,
and `decisions` dates its rows `decided_at` - so the old rule produced a
query referencing a column that does not exist, and the fallback failed
outright rather than degrading.
"""

from app.core.config import Settings
from app.services.mcp.postgres import PostgresKbConnector

# What `_introspect` renders: one `table(col type, col type)` line each.
SCHEMA = "\n".join(
    [
        "articles(id integer, title text, body text, author text, category text, updated_at timestamp)",
        "tickets(id integer, title text, body text, status text, reporter text, created_at timestamp)",
        "runbooks(id integer, title text, body text, owner text, system text, updated_at timestamp)",
        "decisions(id integer, title text, body text, decided_by text, status text, decided_at timestamp)",
    ]
)

TABLES = ["articles", "tickets", "runbooks", "decisions"]


def make_connector(schema: str | None) -> PostgresKbConnector:
    connector = PostgresKbConnector(None, Settings(kb_search_tables=TABLES))
    connector._schema_text = schema
    return connector


def test_columns_come_from_the_schema_not_the_table_name():
    mapping = {table.table: table for table in make_connector(SCHEMA)._fallback_tables()}

    assert mapping["articles"].timestamp_column == "updated_at"
    assert mapping["tickets"].timestamp_column == "created_at"
    assert mapping["runbooks"].timestamp_column == "updated_at"
    assert mapping["decisions"].timestamp_column == "decided_at"

    assert mapping["articles"].author_column == "author"
    assert mapping["tickets"].author_column == "reporter"
    assert mapping["runbooks"].author_column == "owner"
    assert mapping["decisions"].author_column == "decided_by"


def test_unknown_schema_omits_optional_columns_rather_than_guessing():
    """A missing ORDER BY costs an ordering. A column that does not exist
    costs the whole query, which is the failure this avoids."""
    for table in make_connector("")._fallback_tables():
        assert table.timestamp_column is None
        assert table.author_column is None
        # The required columns still have to be named for the query to build.
        assert table.snippet_column == "body"


def test_a_table_missing_from_the_schema_is_not_given_columns():
    """Introspection can succeed for some tables and fail for others - one
    `get_object_details` call per table, and a table the role cannot see
    simply yields no line."""
    partial = "articles(id integer, title text, body text, author text, updated_at timestamp)"
    mapping = {table.table: table for table in make_connector(partial)._fallback_tables()}

    assert mapping["articles"].timestamp_column == "updated_at"
    assert mapping["decisions"].timestamp_column is None
    assert mapping["decisions"].author_column is None


def test_snippet_column_falls_back_through_the_candidates():
    schema = "notes(id integer, title text, content text, created_at timestamp)"
    connector = PostgresKbConnector(None, Settings(kb_search_tables=["notes"]))
    connector._schema_text = schema

    (table,) = connector._fallback_tables()
    assert table.snippet_column == "content"
    assert table.timestamp_column == "created_at"


def test_explicit_config_still_wins_over_introspection():
    """A deployment pointing this at its own tables must not have its
    mapping second-guessed by the resolver."""

    class FakeSource:
        config = {"fallback_tables": [{"table": "docs", "snippet_column": "text_body"}]}

    connector = PostgresKbConnector(FakeSource(), Settings(kb_search_tables=["docs"]))
    connector._schema_text = SCHEMA

    (table,) = connector._fallback_tables()
    assert table.table == "docs"
    assert table.snippet_column == "text_body"
