"""Turning a question into a SELECT, and refusing to run anything else.

Read this before changing it: **the validator here is not the security
boundary.** It is one of four layers, and the only two that actually hold
are the ones that do not depend on parsing SQL correctly:

  1. Grounding    - the model is given the real schema, so it does not guess.
  2. This module  - a syntactic check that rejects the obvious.
  3. postgres-mcp - `--access-mode=restricted`, which parses statements with
                    pglast and refuses non-read-only ones.
  4. The database - the sidecar connects as `kb_ro`: SELECT-only, five
                    second statement timeout, `default_transaction_read_only`,
                    against a database that does not contain the application
                    tables and which kb_ro cannot even CONNECT to.

Layer 4 is why a prompt injection buried in an indexed document is not a
vulnerability: the SQL it induces simply fails. If you find yourself
strengthening the regexes below because you are worried about a bypass, fix
layer 4 instead - a regex over SQL will always be beatable and this one is
only meant to give a clear error and save a round-trip.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("llmhell.text2sql")

MAX_ROWS = 50

# Word-boundary matched so that a column called `updated_at` does not trip
# the `update` rule, and a document about "dropping support" does not trip
# `drop`.
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|create|grant|revoke|truncate|"
    r"copy|vacuum|analyze|reindex|cluster|listen|notify|call|do|"
    r"pg_read_file|pg_read_binary_file|pg_ls_dir|pg_sleep|dblink|lo_import|lo_export"
    r")\b",
    re.IGNORECASE,
)
_STARTS_WITH_SELECT = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_HAS_LIMIT = re.compile(r"\blimit\s+\d+", re.IGNORECASE)


class SqlRejected(ValueError):
    """The generated SQL failed validation and was not sent anywhere."""


@dataclass
class GeneratedQuery:
    """What the model is asked to produce: a query plus which of its columns
    mean what, so results can be mapped to hits without guessing."""

    sql: str
    table: str
    id_column: str
    title_column: str
    snippet_column: str
    timestamp_column: str | None = None
    # "llm" when the model produced it, "fallback" when generation failed
    # and the deterministic query was used instead. Surfaced in the UI - the
    # difference is one of the more interesting things this demo shows.
    mode: str = "llm"
    author_column: str | None = None


def mask_string_literals(sql: str) -> str:
    """Blank out the contents of single-quoted literals, preserving length.

    Every check below scans for `;`, `--` and keywords, and none of those
    mean anything inside a string. Searching the knowledge base for
    "DROP TABLE" or for a term containing a semicolon is a perfectly
    ordinary thing to do, and without this the validator refuses it - a
    false positive that breaks real searches while adding no safety, since
    an escaped quote never escapes the literal in the first place.

    `''` (SQL's escaped quote) reads as close-then-open, which leaves the
    in/out state correct and the content masked either way.
    """
    out: list[str] = []
    in_string = False
    for char in sql:
        if char == "'":
            in_string = not in_string
            out.append(char)
        elif in_string:
            # Keep newlines so line-comment stripping and error offsets stay
            # aligned with the original text.
            out.append("\n" if char == "\n" else " ")
        else:
            out.append(char)
    return "".join(out)


def strip_sql_comments(sql: str) -> str:
    """Remove -- line and /* block */ comments.

    Not cosmetic: `SELECT 1 -- \\n; DROP TABLE x` hides a second statement
    behind a comment, and the statement-count check has to see the real text.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", without_block)


def _cte_names(sql: str) -> set[str]:
    """Names bound by a WITH clause.

    `WITH recent AS (...) SELECT * FROM recent` references `recent` in a
    FROM, but it is not a table and will never appear in the whitelist.
    Treating it as one would reject every CTE query.
    """
    return {match.group(1).lower() for match in re.finditer(r"\b([A-Za-z_]\w*)\s+AS\s*\(", sql, re.IGNORECASE)}


def validate_sql(sql: str, *, allowed_tables: list[str]) -> str:
    """Returns the SQL to run, or raises SqlRejected.

    Note the returned string is derived from the ORIGINAL text - masking and
    comment-stripping happen only on a scratch copy used for inspection.
    """
    if not sql or not sql.strip():
        raise SqlRejected("empty SQL")

    bare = sql.strip().rstrip(";").strip()
    # Literals first, so a comment marker inside a string is not stripped and
    # a semicolon inside one is not counted.
    inspected = strip_sql_comments(mask_string_literals(bare)).strip().rstrip(";").strip()

    if not _STARTS_WITH_SELECT.match(inspected):
        raise SqlRejected("only SELECT/WITH statements are allowed")

    # After stripping one trailing semicolon, any remaining one outside a
    # string literal means a second statement was smuggled in.
    if ";" in inspected:
        raise SqlRejected("only a single statement is allowed")

    forbidden = _FORBIDDEN.search(inspected)
    if forbidden:
        raise SqlRejected(f"forbidden keyword: {forbidden.group(1).lower()}")

    if allowed_tables:
        referenced = {
            match.group(1).split(".")[-1].strip('"').lower()
            for match in re.finditer(r"\b(?:from|join)\s+([A-Za-z_][\w.\"]*)", inspected, re.IGNORECASE)
        }
        allowed = {t.lower() for t in allowed_tables} | _cte_names(inspected)
        unknown = referenced - allowed
        if unknown:
            raise SqlRejected(f"table(s) not in the whitelist: {', '.join(sorted(unknown))}")

    if not _HAS_LIMIT.search(inspected):
        bare = f"{bare} LIMIT {MAX_ROWS}"

    return bare


def wrap_for_json(sql: str) -> str:
    """Wrap a validated SELECT so Postgres does the serialising.

    postgres-mcp returns `str(rows)` - a Python repr - and for any timestamp
    column that repr contains `datetime.datetime(...)`, which is a
    constructor call and therefore parseable by neither `json.loads` nor
    `ast.literal_eval`. Making Postgres emit JSON as *text* means the repr
    that comes back holds a single plain string literal, which parses
    cleanly, and timestamps arrive as ISO-8601.

    See docs/mcp-spike-findings.md for the captured before/after.
    """
    return f"SELECT json_agg(t)::text AS payload FROM ({sql}) t"


def parse_wrapped_rows(payload: Any) -> list[dict[str, Any]]:
    """Unpack what `wrap_for_json` produces: `[{'payload': '<json>'}]`.

    `json_agg` over an empty set returns SQL NULL, not `[]`, so a search
    with no matches arrives as `[{'payload': None}]`. Every shape that is
    not recognisable returns an empty list rather than raising - one
    source's odd response must not fail the federated search.
    """
    if not isinstance(payload, list) or not payload:
        return []
    first = payload[0]
    if not isinstance(first, dict):
        return []
    inner = first.get("payload")
    if inner is None:
        return []
    if isinstance(inner, list):
        return [row for row in inner if isinstance(row, dict)]
    if isinstance(inner, str):
        try:
            decoded = json.loads(inner)
        except (json.JSONDecodeError, ValueError):
            logger.warning("KB payload was not JSON: %.500s", inner)
            return []
        return [row for row in decoded if isinstance(row, dict)] if isinstance(decoded, list) else []
    return []


SYSTEM_PROMPT = """\
You translate a user's question into ONE PostgreSQL SELECT statement over the \
schema below. Reply with a single JSON object and nothing else.

{{"sql": "...", "table": "...", "id_column": "...", "title_column": "...", \
"snippet_column": "...", "timestamp_column": null, "author_column": null}}

Rules:
- SELECT only. One statement. No semicolons, no CTEs that write, no functions \
that touch the filesystem.
- Only these tables may be referenced: {tables}
- Include LIMIT {max_rows} or fewer.
- The SELECT must return the columns you name in id_column, title_column and \
snippet_column, plus timestamp_column and author_column when you name them.
- Match text case-insensitively with ILIKE and % wildcards. Prefer matching \
several columns with OR over guessing one.
- Order by relevance if you can, otherwise by the timestamp column descending.

Schema:
{schema}\
"""


def build_prompt(question: str, *, schema: str, tables: list[str]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                tables=", ".join(tables) or "(none configured)",
                max_rows=MAX_ROWS,
                schema=schema or "(schema unavailable)",
            ),
        },
        {"role": "user", "content": question},
    ]


def parse_generated(content: str, *, allowed_tables: list[str]) -> GeneratedQuery:
    """Read the model's JSON reply and validate the SQL inside it.

    Tolerates a fenced code block and surrounding prose, because models
    routinely add both however firmly the prompt says not to.
    """
    text = (content or "").strip()

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SqlRejected(f"model did not return JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SqlRejected("model returned JSON that is not an object")

    for required in ("sql", "table", "id_column", "title_column", "snippet_column"):
        if not data.get(required):
            raise SqlRejected(f"missing {required!r} in the model's response")

    if allowed_tables and str(data["table"]).lower() not in {t.lower() for t in allowed_tables}:
        raise SqlRejected(f"table {data['table']!r} is not in the whitelist")

    return GeneratedQuery(
        sql=validate_sql(str(data["sql"]), allowed_tables=allowed_tables),
        table=str(data["table"]),
        id_column=str(data["id_column"]),
        title_column=str(data["title_column"]),
        snippet_column=str(data["snippet_column"]),
        timestamp_column=str(data["timestamp_column"]) if data.get("timestamp_column") else None,
        author_column=str(data["author_column"]) if data.get("author_column") else None,
        mode="llm",
    )


@dataclass
class FallbackTable:
    """Enough about a table to search it without asking a model anything."""

    table: str
    id_column: str = "id"
    title_column: str = "title"
    snippet_column: str = "body"
    timestamp_column: str | None = None
    author_column: str | None = None
    search_columns: list[str] = field(default_factory=list)


def escape_literal(value: str) -> str:
    """Single-quote escaping for a string literal.

    Used only for the deterministic fallback, whose shape is fixed and whose
    only variable is the user's search term. Parameter binding is not
    available: postgres-mcp's `execute_sql` takes a SQL string and nothing
    else.
    """
    return value.replace("'", "''")


def build_fallback_query(question: str, table: FallbackTable, *, limit: int) -> GeneratedQuery:
    """A plain ILIKE search, used when the model is unavailable or its
    output fails validation.

    The point is that the Postgres column is never empty merely because a
    model fumbled its JSON - a demo where one source intermittently vanishes
    looks broken, and this failure mode is entirely avoidable.
    """
    term = escape_literal(question.strip())[:200]
    columns = table.search_columns or [table.title_column, table.snippet_column]
    where = " OR ".join(f"{column} ILIKE '%{term}%'" for column in columns)

    selected = [table.id_column, table.title_column, table.snippet_column]
    if table.timestamp_column:
        selected.append(table.timestamp_column)
    if table.author_column:
        selected.append(table.author_column)

    order = f" ORDER BY {table.timestamp_column} DESC" if table.timestamp_column else ""
    sql = (
        f"SELECT {', '.join(dict.fromkeys(selected))} FROM {table.table} "
        f"WHERE {where}{order} LIMIT {min(limit, MAX_ROWS)}"
    )

    return GeneratedQuery(
        sql=sql,
        table=table.table,
        id_column=table.id_column,
        title_column=table.title_column,
        snippet_column=table.snippet_column,
        timestamp_column=table.timestamp_column,
        author_column=table.author_column,
        mode="fallback",
    )
