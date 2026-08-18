"""Searching a Postgres knowledge base through postgres-mcp.

That server exposes nine tools and **none of them searches** - it is a
schema-introspection and DBA-tuning server. So "searching Postgres" here
means asking the answer model to write a SELECT, then running it through
`execute_sql`.

Three quirks of this particular server shape the code, all found by probing
it rather than by reading its README (docs/mcp-spike-findings.md):

  1. It speaks SSE, not streamable HTTP.
  2. It never populates `structuredContent`; every response is a Python
     `repr()` in a text block. For raw query results that repr contains
     `datetime.datetime(...)` constructor calls, which no safe parser will
     touch - hence `wrap_for_json`, which makes Postgres emit JSON as text.
  3. A refused statement comes back as a *successful* result whose text
     starts with "Error: ", so `text_error_prefixes` is opted into here.
"""

import logging
import time
from typing import Any

from app.core.config import Settings
from app.models.source import Source
from app.schemas.search import SearchHit
from app.services.llm import chat
from app.services.mcp.connector import (
    SearchContext,
    SourceResult,
    excerpt_around,
    parse_timestamp,
)
from app.services.mcp.transport import (
    McpError,
    call_tool,
    list_tool_names,
    sse_session,
    summarise_exception,
)
from app.services.search.text2sql import (
    FallbackTable,
    GeneratedQuery,
    SqlRejected,
    build_fallback_query,
    build_prompt,
    escape_literal,
    parse_generated,
    parse_wrapped_rows,
    validate_sql,
    wrap_for_json,
)

logger = logging.getLogger("llmhell.mcp.postgres")

_ERROR_PREFIXES = ("Error:",)
SNIPPET_CHARS = 400

# Turning a question into a SELECT is a mechanical translation with the schema
# already in the prompt - there is nothing here worth deliberating over, and a
# reasoning model that deliberates anyway is a straight loss.
#
# It is also the failure mode this hit in practice. DeepSeek V4 is served with
# `enable_thinking: true` by default, so it spent the entire 512-token budget
# reasoning and returned `finish_reason: length` with a null content. The JSON
# never arrived, `parse_generated` rejected the empty string, and the source
# silently degraded to its ILIKE fallback on every single search - eight
# seconds spent to produce nothing.
#
# vLLM applies chat_template_kwargs per request, so this disables thinking for
# this call alone; the answer itself still reasons, which is where it earns
# its keep. On a server whose template has no such flag the field is ignored,
# which is why the token budget is raised as well rather than instead.
_NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
TEXT2SQL_MAX_TOKENS = 1024

# Candidates for the deterministic fallback, most specific first. Only used
# when a column of that role is actually present in the introspected schema.
_SNIPPET_COLUMNS = ("body", "content", "description", "summary", "text")
_TIMESTAMP_COLUMNS = ("updated_at", "created_at", "decided_at", "occurred_at", "published_at")
_AUTHOR_COLUMNS = ("author", "reporter", "owner", "decided_by", "created_by")


def _prefer(candidates: tuple[str, ...], available: set[str] | None) -> str | None:
    """First candidate the table actually has, or None if the schema is
    unknown - never a guess."""
    if not available:
        return None
    return next((name for name in candidates if name in available), None)


class PostgresKbConnector:
    """Implements `Connector` for the Postgres knowledge base."""

    kind = "postgres"

    def __init__(self, source: Source | None, settings: Settings, *, key: str = "postgres_kb"):
        self.key = key
        self._settings = settings
        self._source = source
        # Cached compact DDL, so the model is grounded in the real schema
        # instead of guessing column names. Populated lazily on first search
        # and reused; a wrong guess here costs a whole failed search.
        self._schema_text: str | None = None

    # --- configuration ----------------------------------------------------

    @property
    def _tables(self) -> list[str]:
        configured = (self._source.config or {}).get("tables") if self._source else None
        return list(configured or self._settings.kb_search_tables)

    def _schema_columns(self) -> dict[str, set[str]]:
        """Column names per table, read back out of the introspected DDL.

        `_introspect` renders each table as `name(col type, col type)`, which
        is the only description of the real schema this class has. Parsing it
        back is cheaper than a second round-trip and cannot disagree with
        what the model was shown.
        """
        columns: dict[str, set[str]] = {}
        for line in (self._schema_text or "").splitlines():
            table, _, rendered = line.partition("(")
            if not table or not rendered.endswith(")"):
                continue
            columns[table.strip()] = {
                part.strip().split(" ")[0] for part in rendered[:-1].split(",") if part.strip()
            }
        return columns

    def _fallback_tables(self) -> list[FallbackTable]:
        """Column mapping for the deterministic fallback.

        Read from the source config when present, so a deployment can point
        this at its own tables. Otherwise the columns are resolved against
        the introspected schema by preference, rather than assumed from the
        table name: `articles` dates its rows `updated_at`, `tickets` uses
        `created_at`, and `decisions` uses `decided_at`, so any rule keyed on
        the name is wrong for the next table somebody adds.

        When introspection failed there is no schema to resolve against, and
        the optional columns are left unset. A missing ORDER BY costs an
        ordering; a guessed column name that does not exist costs the whole
        query.
        """
        configured = (self._source.config or {}).get("fallback_tables") if self._source else None
        if configured:
            return [FallbackTable(**entry) for entry in configured]

        schema = self._schema_columns()
        return [
            FallbackTable(
                table=table,
                snippet_column=_prefer(_SNIPPET_COLUMNS, schema.get(table)) or "body",
                timestamp_column=_prefer(_TIMESTAMP_COLUMNS, schema.get(table)),
                author_column=_prefer(_AUTHOR_COLUMNS, schema.get(table)),
            )
            for table in self._tables
        ]

    # --- health -----------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        try:
            async with sse_session(self._settings.postgres_mcp_url) as session:
                tools = await list_tool_names(session, timeout=self._settings.mcp_init_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return {"ok": False, "error": summarise_exception(exc), "url": self._settings.postgres_mcp_url}

        return {
            "ok": True,
            "url": self._settings.postgres_mcp_url,
            "transport": "sse",
            "tools": tools,
            "has_execute_sql": "execute_sql" in tools,
            "tables": self._tables,
        }

    # --- search -----------------------------------------------------------

    async def search(self, query: str, *, limit: int, ctx: SearchContext) -> SourceResult:
        started = time.monotonic()
        result = SourceResult(source_key=self.key)

        tables = self._tables
        if not tables:
            result.error = "no tables configured (set KB_SEARCH_TABLES)"
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        try:
            async with sse_session(self._settings.postgres_mcp_url) as session:
                await self._ensure_schema(session, tables)
                generated = await self._build_query(query, ctx=ctx, limit=limit)
                rows = await self._run(session, generated)
        except Exception as exc:  # noqa: BLE001 - isolation is the contract
            logger.warning("postgres search failed: %s", summarise_exception(exc))
            result.error = summarise_exception(exc)
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        result.hits = self._to_hits(rows, generated, query=query, debug=ctx.debug)
        result.detail = {
            "mode": generated.mode,
            "sql": generated.sql,
            "table": generated.table,
            "rows": len(rows),
        }
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    async def _ensure_schema(self, session, tables: list[str]) -> None:
        if self._schema_text is not None:
            return
        try:
            self._schema_text = await self._introspect(session, tables)
        except Exception as exc:  # noqa: BLE001
            # Not fatal: an ungrounded prompt is worse than a grounded one,
            # but the deterministic fallback needs no schema at all.
            logger.warning("KB schema introspection failed: %s", exc)
            self._schema_text = ""

    async def _introspect(self, session, tables: list[str]) -> str:
        """A compact DDL-ish description of the whitelisted tables."""
        lines: list[str] = []
        for table in tables:
            raw = await call_tool(
                session,
                "get_object_details",
                {"schema_name": "public", "object_name": table, "object_type": "table"},
                timeout=self._settings.mcp_call_timeout_seconds,
                text_error_prefixes=_ERROR_PREFIXES,
            )
            payload = raw.payload(source=self.key, tool="get_object_details")
            if not isinstance(payload, dict):
                continue
            columns = payload.get("columns")
            if not isinstance(columns, list):
                continue
            rendered = ", ".join(
                f"{column.get('column')} {column.get('data_type')}"
                for column in columns
                if isinstance(column, dict) and column.get("column")
            )
            lines.append(f"{table}({rendered})")
        return "\n".join(lines)

    async def _build_query(self, query: str, *, ctx: SearchContext, limit: int) -> GeneratedQuery:
        """Model-generated SQL, falling back to a deterministic ILIKE.

        The fallback is not a nicety. Without it this source disappears from
        the results whenever the model is down or returns malformed JSON,
        which in a demo reads as "the integration is broken".
        """
        fallback_tables = self._fallback_tables()

        if ctx.answer_endpoint is not None:
            try:
                completion = await chat.complete(
                    ctx.answer_endpoint,
                    build_prompt(query, schema=self._schema_text or "", tables=self._tables),
                    http_client=ctx.http_client,
                    max_tokens=TEXT2SQL_MAX_TOKENS,
                    temperature=0.0,
                    extra_body=_NO_THINKING,
                )
                return parse_generated(completion.content, allowed_tables=self._tables)
            except (chat.ChatError, SqlRejected) as exc:
                logger.info("text2sql fell back to a deterministic query: %s", exc)

        if not fallback_tables:
            raise McpError("no fallback table mapping configured")
        return build_fallback_query(query, fallback_tables[0], limit=limit)

    async def _run(self, session, generated: GeneratedQuery) -> list[dict[str, Any]]:
        # Re-validate even the fallback. It is built from a template, but
        # validating exactly what is about to run - rather than what we
        # believe was built - is the only check that cannot drift.
        safe_sql = validate_sql(generated.sql, allowed_tables=self._tables)
        raw = await call_tool(
            session,
            "execute_sql",
            {"sql": wrap_for_json(safe_sql)},
            timeout=self._settings.mcp_call_timeout_seconds,
            text_error_prefixes=_ERROR_PREFIXES,
        )
        return parse_wrapped_rows(raw.payload(source=self.key, tool="execute_sql"))

    def _to_hits(
        self,
        rows: list[dict[str, Any]],
        generated: GeneratedQuery,
        *,
        query: str,
        debug: bool,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for rank, row in enumerate(rows):
            pk = row.get(generated.id_column)
            if pk is None:
                continue
            hits.append(
                SearchHit(
                    id=f"{self.key}:{generated.table}:{pk}",
                    source=self.key,
                    kind="row",
                    external_id=str(pk),
                    title=str(row.get(generated.title_column) or f"{generated.table} #{pk}"),
                    snippet=excerpt_around(str(row.get(generated.snippet_column) or ""), query, SNIPPET_CHARS),
                    # A database row has no natural URL, so one is
                    # synthesised and served by GET /api/records - which
                    # resolves the table against the whitelist rather than
                    # trusting the path.
                    url=self._settings.kb_row_link_template.format(table=generated.table, pk=pk),
                    author=str(row.get(generated.author_column)) if generated.author_column else None,
                    timestamp=parse_timestamp(row.get(generated.timestamp_column))
                    if generated.timestamp_column
                    else None,
                    rank_in_source=rank,
                    raw=row if debug else None,
                )
            )
        return hits

    # --- single record ----------------------------------------------------

    async def fetch_content(self, hit_id: str) -> dict[str, Any] | None:
        """The whole row as readable text, for the content viewer.

        Search shows a 400-character excerpt centred on the match, which is
        enough to judge relevance and not enough to read. This reuses
        `fetch_record`, so the table whitelist and key escaping that protect
        the record route protect this too - there is one way into this
        database and it is that method.
        """
        parts = hit_id.split(":")
        if len(parts) != 3 or parts[0] != self.key:
            return None

        row = await self.fetch_record(parts[1], parts[2])
        if row is None:
            return None

        # Rendered rather than dumped as JSON: this is for reading, and a
        # row's long prose column is the reason anyone opened it.
        body = "\n\n".join(f"{column}:\n{value}" for column, value in row.items() if value not in (None, ""))
        return {
            "title": f"{parts[1]} #{parts[2]}",
            "text": body,
            "language": None,
            "truncated": False,
        }

    async def fetch_record(self, table: str, pk: str) -> dict[str, Any] | None:
        """Back the synthesised row links.

        `table` is matched against the whitelist rather than interpolated
        from the request, and the primary key is escaped and length-capped -
        without both, this route would be an arbitrary-read primitive over
        whatever kb_ro can see.
        """
        from app.services.mcp.transport import call_tool

        match = next((t for t in self._tables if t.lower() == table.lower()), None)
        if match is None:
            return None


        id_column = next(
            (t.id_column for t in self._fallback_tables() if t.table.lower() == match.lower()), "id"
        )
        # ::text so a non-numeric primary key does not make Postgres raise
        # on the comparison, and so the escaped literal is always valid.
        sql = f"SELECT * FROM {match} WHERE {id_column}::text = '{escape_literal(str(pk)[:64])}' LIMIT 1"

        async with sse_session(self._settings.postgres_mcp_url) as session:
            raw = await call_tool(
                session,
                "execute_sql",
                {"sql": wrap_for_json(validate_sql(sql, allowed_tables=self._tables))},
                timeout=self._settings.mcp_call_timeout_seconds,
                text_error_prefixes=_ERROR_PREFIXES,
            )
        rows = parse_wrapped_rows(raw.payload(source=self.key, tool="execute_sql"))
        return rows[0] if rows else None
