"""Searching Google Workspace through aaronsb/google-workspace-mcp.

What makes this the awkward source of the three:

  * **It is stdio-only.** No HTTP transport exists, so it cannot simply be a
    sidecar the API dials. It is either a child process of the API, or it is
    bridged - `docker/google-mcp/` wraps it with supergateway so it speaks
    streamable HTTP like the others. `GOOGLE_MCP_MODE` picks which, and this
    connector handles both by routing every call through `_call`.

  * **First use needs an interactive browser consent**, which cannot happen
    in a container. Tokens are produced on a workstation and mounted in; see
    docs/google-workspace-setup.md.

Four things about its contract, all confirmed against a running v4.2.1 rather
than assumed - every one of them was wrong in an earlier version of this file:

  1. **It answers in Markdown, not JSON.** `structuredContent` is always
     null and the text block is a human-readable report: a `## Files (N)`
     heading, pipe-delimited rows, then "Next steps" and "Session context"
     prose. Nothing here can be parsed as JSON or a Python literal, so the
     usual payload ladder yields nothing and this module parses the table
     itself. See `parse_markdown_rows`.
  2. **`email` is REQUIRED** on every tool. The server is multi-account and
     has no default, so there is nothing to fall back to when it is unset.
  3. **Drive's `query` is Drive's query language**, not free text: a bare
     phrase is a syntax error, not a search. See `drive_query`.
  4. **Search returns metadata only - no content and no link.** A citation
     needs something to quote, so the top few hits are enriched with a second
     call that fetches the document text. See `_enrich`.

The advertised schemas say `additionalProperties: false`, but the runtime
ignores unknown keys - a stray `pageSize` was accepted, not rejected. We send
only documented arguments anyway; do not rely on the server to catch a typo.

Note it does NOT shell out to `gws`, Google's Workspace CLI, contrary to most
writing about this package: it calls Google's REST APIs directly with its own
tokens. See the header of docker/google-mcp/Dockerfile.

The tools are fat operation-dispatchers rather than one tool per action, so
searching Drive is `manage_drive {operation: "search"}`.

A row that cannot be mapped is dropped rather than raising, and when a
response matches no known shape at all `search()` reports that in
`detail["warning"]` instead of quietly showing an empty list.
"""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.models.source import SOURCE_GOOGLE_DRIVE, SOURCE_GOOGLE_MAIL, Source
from app.schemas.search import SearchHit
from app.services.llm import vision
from app.services.mcp.connector import (
    SearchContext,
    SourceResult,
    excerpt_around,
    parse_timestamp,
    search_terms,
    truncate,
)
from app.services.mcp.transport import (
    McpError,
    RawToolResult,
    call_tool,
    http_session,
    list_tool_names,
    summarise_exception,
)
from app.services.pdf import page_stats, render_pages, select_visual_pages
from app.services.search import page_index

logger = logging.getLogger("llmhell.mcp.google")

SNIPPET_CHARS = 400

# Both tools cap this at 50 and default to 10.
MAX_RESULTS = 50

# What the content viewer will show of one document. Generous, since the
# point is to read it, but still bounded - this crosses the API in one
# response.
MAX_CONTENT_CHARS = 200_000

# Which fat tool serves which source, and what a hit from it is.
_SURFACES: dict[str, dict[str, Any]] = {
    SOURCE_GOOGLE_DRIVE: {"tool": "manage_drive", "kind": "document"},
    SOURCE_GOOGLE_MAIL: {"tool": "manage_email", "kind": "email"},
}


def escape_drive_term(text: str) -> str:
    """Drive's grammar delimits terms with single quotes and escapes with a
    backslash."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


_DRIVE_MAX_TERMS = 4
# Gmail ORs these in one expression, so this is a query-length budget rather
# than a round-trip one, and can afford to be the same as Drive's.
_GMAIL_MAX_TERMS = 4


def drive_query(text: str) -> str:
    """Turn a user's words into a Drive API query.

    `manage_drive`'s `query` is not free text - it is Drive's own query
    language, as its own description says: `name contains 'budget'`,
    `mimeType='application/pdf'`. Passing a bare phrase is a syntax error, not
    a search.

    `fullText contains` searches name, content and metadata, which is the
    closest thing to what someone typing into a search box means.

    The terms are OR-ed rather than passed as one string, and that is the
    whole point of this function. `fullText contains 'test document'` is an
    exact-PHRASE search: a document titled "TEST" full of prose does not match
    it, and neither does anything else a person actually types. Asking "find
    the test document on Drive" returned zero results while the document sat
    there in plain sight - the source looked broken when it was merely being
    asked the wrong question.

    Terms are capped because Drive's query length is not unlimited and a long
    question is mostly filler; the cap keeps the most specific words, since
    the ones that carry a query are rarely the short ones.
    """
    terms = search_terms(text, limit=_DRIVE_MAX_TERMS)

    if not terms:
        # Nothing usable was left - fall back to the raw text rather than
        # emitting an empty expression, which is a syntax error.
        stripped = (text or "").strip()
        if not stripped:
            return "trashed = false"
        return f"fullText contains '{escape_drive_term(stripped)}'"

    clauses = " or ".join(f"fullText contains '{escape_drive_term(term)}'" for term in terms)
    # Trashed files still match fullText, and offering someone a deleted
    # document as a source is worse than offering nothing.
    return f"({clauses}) and trashed = false"


# Gmail's own operators. A query containing one was written by someone who
# knows the syntax, and reducing it to terms would destroy their intent -
# `from:anna` would become a search for the words "from" and "anna".
_GMAIL_OPERATORS = re.compile(
    r"\b(from|to|cc|bcc|subject|label|in|is|has|filename|list|deliveredto|"
    r"after|before|older|newer|older_than|newer_than|size|larger|smaller|category):",
    re.IGNORECASE,
)


def gmail_query(text: str) -> str:
    """Turn a question into a Gmail search.

    Gmail accepts bare terms, which made this a passthrough for a long time -
    and passing the whole question through is exactly the bug. Gmail ANDs
    bare terms, so "what does the Gmail team say about the inbox" requires
    every one of those words to appear in the same message and matches
    nothing, even though "inbox" and "Gmail app" each match on their own.

    `{a b}` is Gmail's OR, so the terms are joined that way. A query already
    using Gmail's operators is passed through untouched, minus double quotes
    which would unbalance the expression.
    """
    cleaned = (text or "").replace('"', " ").strip()
    if not cleaned or _GMAIL_OPERATORS.search(cleaned):
        return cleaned

    terms = search_terms(cleaned, limit=_GMAIL_MAX_TERMS)
    if not terms:
        return cleaned
    if len(terms) == 1:
        return terms[0]
    return "{" + " ".join(terms) + "}"


# --- Markdown report parsing -----------------------------------------------
#
# The real response shape. A Drive search looks like:
#
#     ## Files (1)
#
#     1AbCdEfG…9abc | TEST | g/document | Aug 17 | 2.2 KB
#
#     ---
#     **Next steps:**
#     - Get file details: …
#
# and a Gmail search the same but `## Messages (N)` with different columns.

_HEADING = re.compile(r"^##\s+(Files|Messages)\s*\((\d+)\)\s*$", re.MULTILINE)
# "Aug 17" (this year, implicitly) or "Aug 17, 2024" for older items.
_SHORT_DATE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})(?:,\s*(\d{4}))?$")

_MONTHS = {
    name: number
    for number, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
    )
}

# Drive abbreviates the mime type. Each Google type has its own edit URL;
# a generic file has none and needs the Drive viewer instead.
_DRIVE_URL_BY_TYPE = {
    "g/document": "https://docs.google.com/document/d/{id}/edit",
    "g/spreadsheet": "https://docs.google.com/spreadsheets/d/{id}/edit",
    "g/presentation": "https://docs.google.com/presentation/d/{id}/edit",
    "g/form": "https://docs.google.com/forms/d/{id}/edit",
    "g/drawing": "https://docs.google.com/drawings/d/{id}/edit",
    "g/folder": "https://drive.google.com/drive/folders/{id}",
}
_DRIVE_URL_DEFAULT = "https://drive.google.com/file/d/{id}/view"

# The abbreviated type the search report puts in its third column. Google's
# own formats are "g/document", "g/spreadsheet"; everything else is a plain
# extension, so a PDF reads simply "pdf".
_PDF_TYPE_HINTS = frozenset(["pdf", "application/pdf"])

# `download` answers with a report, not the bytes:
#
#     **name.pdf** saved to workspace
#
#     **Path:** /data/share/google-workspace-mcp/workspace/name.pdf
#     **Size:** 373000 bytes
#
_DOWNLOAD_PATH = re.compile(r"^\*\*Path:\*\*\s*(\S.*?)\s*$", re.MULTILINE)


def is_pdf(type_hint: str | None) -> bool:
    return (type_hint or "").strip().lower() in _PDF_TYPE_HINTS


_DRIVE_TYPE = re.compile(r"^\*\*Type:\*\*\s*(\S+)\s*$", re.MULTILINE)


def parse_drive_type(text: str) -> str | None:
    """The mime type out of a `manage_drive get` report.

    Search remembers each file's type as it goes, but that memory is a
    process attribute: the viewer can be opened on a link days later, or
    after a restart, with nothing cached. Asking the server is one call and
    it is the difference between the viewer working and the viewer being
    mysteriously empty for exactly the files that needed it most.
    """
    match = _DRIVE_TYPE.search(text or "")
    return match.group(1) if match else None


def drive_type_hints(text: str) -> dict[str, str]:
    """file id -> the report's abbreviated type, for the rows in a search.

    `drive_hits_from_markdown` reads the same rows and throws this column
    away after choosing a URL, but enrichment needs it: a Google Doc is read
    with `manage_docs`, a PDF has to be downloaded and parsed, and nothing
    else on a `SearchHit` distinguishes them.
    """
    hints: dict[str, str] = {}
    for row in parse_markdown_rows(text):
        if len(row) > 2 and row[0]:
            hints[row[0]] = row[2]
    return hints


def parse_download_path(text: str) -> str | None:
    """The path `manage_drive download` says it wrote to.

    No bytes cross the MCP boundary - the server saves the file into its own
    workspace and reports where. That directory is bind-mounted into this
    container at the same path, so the answer is directly openable.
    """
    match = _DOWNLOAD_PATH.search(text or "")
    return match.group(1) if match else None


def extract_pdf_text(data: bytes, *, limit: int) -> str:
    """A PDF's text layer, or "" when it has none.

    Imported lazily: pypdf is only needed when a PDF actually turns up in
    results, and paying its import on every API start for a source that may
    not even be configured is not worth it.

    A scanned page yields nothing here, which is not an error - it is the
    case the vision model exists for, and returning "" is how this reports
    that rather than by raising.
    """
    from io import BytesIO

    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        logger.info("could not read PDF: %s", exc)
        return ""

    parts: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page should not lose the rest
            continue
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= limit:
            break
    return "\n\n".join(parts)[:limit]


def parse_markdown_rows(text: str) -> list[list[str]]:
    """Pipe-delimited rows from a `## Files (N)` / `## Messages (N)` report.

    Stops at the first `---`, which is where the server's "Next steps" and
    "Session context" prose begins - that boilerplate mentions ids and emails
    of its own and would otherwise be parsed as results.
    """
    match = _HEADING.search(text or "")
    if not match:
        return []

    rows: list[list[str]] = []
    for line in text[match.end() :].splitlines():
        stripped = line.strip()
        if stripped.startswith("---"):
            break
        if not stripped or "|" not in stripped:
            continue
        rows.append([cell.strip() for cell in stripped.split("|")])
    return rows


def parse_short_date(value: str) -> datetime | None:
    """"Aug 17" or "Aug 17, 2024" to a datetime, or None.

    The server omits the year for recent items, the way a mail client does,
    which means it is implicitly "the most recent Aug 17 that has already
    happened". Guessing the current year unconditionally would date a
    December item a year into the future and hand it a recency boost it has
    not earned.
    """
    match = _SHORT_DATE.match((value or "").strip())
    if not match:
        return None
    month = _MONTHS.get(match.group(1))
    if not month:
        return None

    day, year = int(match.group(2)), match.group(3)
    now = datetime.now(UTC)
    try:
        if year:
            return datetime(int(year), month, day, tzinfo=UTC)
        candidate = datetime(now.year, month, day, tzinfo=UTC)
        # Allow a day of slack for timezone skew before assuming last year.
        if candidate > now + timedelta(days=1):
            candidate = datetime(now.year - 1, month, day, tzinfo=UTC)
        return candidate
    except ValueError:
        return None


def drive_url(file_id: str, type_hint: str) -> str:
    template = _DRIVE_URL_BY_TYPE.get((type_hint or "").strip().lower(), _DRIVE_URL_DEFAULT)
    return template.format(id=file_id)


def drive_hits_from_markdown(text: str, *, source_key: str, debug: bool) -> list[SearchHit]:
    """`<fileId> | <name> | <type> | <date> | <size>`"""
    hits: list[SearchHit] = []
    for row in parse_markdown_rows(text):
        if len(row) < 2:
            continue
        file_id, name = row[0], row[1]
        if not file_id or not name:
            continue
        type_hint = row[2] if len(row) > 2 else ""
        hits.append(
            SearchHit(
                id=f"{source_key}:{file_id}",
                source=source_key,
                kind="document",
                external_id=file_id,
                title=name,
                # Search carries no content; _enrich fills this in for the
                # top hits.
                snippet="",
                url=drive_url(file_id, type_hint),
                timestamp=parse_short_date(row[3]) if len(row) > 3 else None,
                rank_in_source=len(hits),
                raw={"row": row} if debug else None,
            )
        )
    return hits


def email_hits_from_markdown(text: str, *, source_key: str, debug: bool) -> list[SearchHit]:
    """`<messageId> | <from> | <subject> | <date>`

    Note the sender is truncated with a "…" by the server when long, so it is
    a display value and not a usable address.
    """
    hits: list[SearchHit] = []
    for row in parse_markdown_rows(text):
        if len(row) < 3:
            continue
        message_id, sender, subject = row[0], row[1], row[2]
        if not message_id or not subject:
            continue
        hits.append(
            SearchHit(
                id=f"{source_key}:{message_id}",
                source=source_key,
                kind="email",
                external_id=message_id,
                title=subject,
                snippet="",
                url=f"https://mail.google.com/mail/u/0/#all/{message_id}",
                author=sender or None,
                timestamp=parse_short_date(row[3]) if len(row) > 3 else None,
                rank_in_source=len(hits),
                raw={"row": row} if debug else None,
            )
        )
    return hits


_HEADING_LINE = re.compile(r"^#{1,6}\s")
_KEY_VALUE_LINE = re.compile(r"^\*\*[^*]+:\*\*")
_BOILERPLATE = ("**Next steps:**", "**Session context**")


def extract_report_body(text: str) -> str:
    """The document or message body from a `get`/`read` response.

    The two shapes differ, which is why this cannot just split on `---`:

      manage_docs get   `## title`, `**Key:** value` lines, `---`, BODY, `---`, boilerplate
      manage_email read `## title`, `**Key:** value` lines, BODY, then boilerplate

    So the body is after the header block in one case and after a rule in the
    other. Rather than special-casing each tool, take the first `---`-section
    that still has real prose once the heading and the `**Key:** value` lines
    are removed.
    """
    for section in re.split(r"(?m)^---\s*$", text or ""):
        if any(marker in section for marker in _BOILERPLATE):
            # Trim at the boilerplate; anything before it may still be body.
            for marker in _BOILERPLATE:
                index = section.find(marker)
                if index != -1:
                    section = section[:index]

        kept = [
            line
            for line in section.splitlines()
            if line.strip() and not _HEADING_LINE.match(line.strip()) and not _KEY_VALUE_LINE.match(line.strip())
        ]
        body = "\n".join(kept).strip()
        if body:
            return body
    return ""


def _first(payload: dict[str, Any], *names: str) -> Any:
    """First present, non-empty value among several candidate field names.

    Used heavily here because the shapes are unverified: `webViewLink` and
    `alternateLink` are both plausible, and guessing one and being wrong
    means every hit silently loses its link.
    """
    for name in names:
        value = payload.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def items_from(payload: Any) -> list[Any]:
    """Unwrap a result into a list of items, whatever it is wrapped in."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("files", "messages", "items", "results", "data", "threads", "documents"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        # A single object rather than a collection.
        if any(key in payload for key in ("id", "name", "title", "subject")):
            return [payload]
    return []


def drive_hit(item: dict[str, Any], rank: int, *, source_key: str, debug: bool) -> SearchHit | None:
    file_id = _first(item, "id", "fileId", "documentId")
    title = _first(item, "name", "title", "originalFilename") or (file_id and f"Drive file {file_id}")
    if not title:
        return None

    url = _first(item, "webViewLink", "alternateLink", "webContentLink", "url", "link")
    if not url and file_id:
        # The API returns ids far more reliably than links, and a Drive file
        # id is enough to build a working URL.
        url = f"https://drive.google.com/file/d/{file_id}/view"

    owners = item.get("owners")
    author = None
    if isinstance(owners, list) and owners:
        first_owner = owners[0]
        author = (
            _first(first_owner, "displayName", "emailAddress")
            if isinstance(first_owner, dict)
            else str(first_owner)
        )
    author = author or _first(item, "lastModifyingUser", "owner", "author")
    if isinstance(author, dict):
        author = _first(author, "displayName", "emailAddress")

    return SearchHit(
        id=f"{source_key}:{file_id or title}",
        source=source_key,
        kind="document",
        external_id=str(file_id) if file_id else None,
        title=str(title),
        snippet=truncate(
            str(_first(item, "snippet", "description", "summary", "mimeType") or ""), SNIPPET_CHARS
        ),
        url=str(url) if url else None,
        author=str(author) if author else None
        ,
        timestamp=parse_timestamp(_first(item, "modifiedTime", "modifiedDate", "updated", "createdTime")),
        rank_in_source=rank,
        raw=item if debug else None,
    )


def email_hit(item: dict[str, Any], rank: int, *, source_key: str, debug: bool) -> SearchHit | None:
    message_id = _first(item, "id", "messageId", "threadId")

    # Gmail returns headers either flattened or as a payload.headers list.
    headers = {}
    payload = item.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("headers"), list):
        headers = {
            str(header.get("name", "")).lower(): header.get("value")
            for header in payload["headers"]
            if isinstance(header, dict)
        }

    subject = _first(item, "subject", "title") or headers.get("subject")
    sender = _first(item, "from", "sender") or headers.get("from")
    if isinstance(sender, dict):
        sender = _first(sender, "emailAddress", "name", "email")

    title = subject or (message_id and f"Message {message_id}")
    if not title:
        return None

    url = _first(item, "webLink", "url", "link")
    if not url and message_id:
        # Gmail's API returns no web link, only ids.
        url = f"https://mail.google.com/mail/u/0/#all/{message_id}"

    return SearchHit(
        id=f"{source_key}:{message_id or title}",
        source=source_key,
        kind="email",
        external_id=str(message_id) if message_id else None,
        title=str(title),
        snippet=truncate(str(_first(item, "snippet", "preview", "bodyPreview", "body") or ""), SNIPPET_CHARS),
        url=str(url) if url else None,
        author=str(sender) if sender else None,
        timestamp=parse_timestamp(
            _first(item, "date", "internalDate", "receivedTime") or headers.get("date")
        ),
        rank_in_source=rank,
        raw=item if debug else None,
    )


class GoogleWorkspaceConnector:
    """One searchable Google surface - Drive or Gmail."""

    kind = "google_workspace"

    def __init__(self, source: Source | None, settings: Settings, *, key: str):
        if key not in _SURFACES:
            raise ValueError(f"unsupported Google surface: {key!r}")
        self.key = key
        self._settings = settings
        self._source = source
        self._tool = _SURFACES[key]["tool"]
        # file id -> abbreviated type from the last search's report. Populated
        # in `_to_hits` and read by enrichment, which has to know whether a
        # hit is a Google Doc or a PDF and has nothing on the hit to tell it.
        self._type_hints: dict[str, str] = {}
        # file id -> name, remembered from the search report so a page stored
        # in the index can be shown with the document's real title rather than
        # its id.
        self._titles: dict[str, str] = {}
        # Documents being read in the background right now, so two
        # searches a second apart do not both render the same pages.
        self._transcribing: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def _account(self) -> str | None:
        configured = (self._source.config or {}).get("account_email") if self._source else None
        return configured or self._settings.google_account_email or None

    def _base_args(self) -> dict[str, Any]:
        # `email` is REQUIRED by both tool schemas - the server is
        # multi-account and has no notion of a default one. There is nothing
        # to fall back to, so a missing account is a configuration error
        # raised before the call rather than a schema rejection after it.
        if not self._account:
            raise McpError(
                "GOOGLE_ACCOUNT_EMAIL is not set, and the Google tools require an account address"
            )
        return {"email": self._account}

    async def _call(self, tool: str, arguments: dict[str, Any]) -> RawToolResult:
        """Route a call through whichever transport is configured.

        In stdio mode the call is handed to the supervisor task that owns the
        subprocess; this coroutine never touches the session itself, because
        an MCP stdio session must be entered and exited in one task.
        """
        timeout = self._settings.mcp_call_timeout_seconds

        if self._settings.google_mcp_mode == "stdio":
            from app.services.mcp.registry import get_mcp_registry

            supervisor = get_mcp_registry().google_supervisor
            if supervisor is None:
                raise McpError("google_mcp_mode=stdio but the supervisor is not running")
            return await supervisor.call(tool, arguments, timeout=timeout)

        async with http_session(self._settings.google_mcp_url) as session:
            return await call_tool(session, tool, arguments, timeout=timeout)

    async def health(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "mode": self._settings.google_mcp_mode,
            "tool": self._tool,
            "account": self._account,
        }

        if not self._account:
            # Not a warning but a hard failure: `email` is a required
            # argument on every Google tool, so without it no call can be
            # made at all.
            report["ok"] = False
            report["error"] = "GOOGLE_ACCOUNT_EMAIL is not set, and every Google tool requires it"
            return report

        try:
            if self._settings.google_mcp_mode == "stdio":
                # There is no session to list tools from without going
                # through the supervisor, and a tool listing is not worth a
                # queue round-trip; reaching the supervisor at all is the
                # signal.
                from app.services.mcp.registry import get_mcp_registry

                running = get_mcp_registry().google_supervisor is not None
                report.update({"ok": running})
                if not running:
                    report["error"] = "the stdio supervisor is not running"
                return report

            async with http_session(self._settings.google_mcp_url) as session:
                tools = await list_tool_names(session, timeout=self._settings.mcp_init_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            report.update({"ok": False, "error": summarise_exception(exc), "url": self._settings.google_mcp_url})
            return report

        report.update(
            {
                "ok": True,
                "url": self._settings.google_mcp_url,
                "tools": tools,
                "has_tool": self._tool in tools,
            }
        )
        if self._tool not in tools:
            report["warning"] = f"the server does not expose {self._tool}"
        return report

    async def search(self, query: str, *, limit: int, ctx: SearchContext) -> SourceResult:
        started = time.monotonic()
        result = SourceResult(source_key=self.key)

        is_drive = self.key == SOURCE_GOOGLE_DRIVE

        try:
            arguments = {
                **self._base_args(),
                "operation": "search",
                "query": drive_query(query) if is_drive else gmail_query(query),
                "maxResults": min(limit, MAX_RESULTS),
            }
            raw = await self._call(self._tool, arguments)
        except Exception as exc:  # noqa: BLE001 - isolation is the contract
            logger.warning("%s search failed: %s", self.key, summarise_exception(exc))
            result.error = summarise_exception(exc)
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        hits, shape = self._to_hits(raw, limit=limit, debug=ctx.debug)

        # Search results carry no body text, so an answer built on them would
        # have titles to cite and nothing to quote. Enriching a bounded number
        # of the top hits is what makes a citation worth following.
        # Guarded separately from the search itself, and this is not belt and
        # braces. Enrichment reads documents, renders pages and calls a second
        # model - far more ways to fail than a search has - and it runs after
        # the results are already in hand. A missing PDF parser once turned a
        # working Drive search into an empty source and an ERROR line saying
        # the connector raised, "which it should not".
        enriched = 0
        if hits and self._settings.google_enrich_hits > 0:
            try:
                enriched = await self._enrich(
                    hits[: self._settings.google_enrich_hits], query=query, ctx=ctx
                )
            except Exception as exc:  # noqa: BLE001 - snippets are a bonus, hits are the result
                logger.warning("%s enrichment failed: %s", self.key, summarise_exception(exc))
                result.degraded = True

        # The other half of the search. Drive indexes a PDF's text layer, so a
        # term printed only inside a diagram never finds its file there -
        # asking for `UART3` returned nothing while the pad was legible on
        # page three. Pages this app has transcribed are searched here, and
        # any document Drive missed is added to the list.
        from_index = 0
        if is_drive:
            try:
                from_index = await self._add_index_hits(
                    hits, query=query, limit=limit, ctx=ctx, debug=ctx.debug
                )
            except Exception as exc:  # noqa: BLE001 - a local index is an addition, never the result
                logger.warning("page index lookup failed: %s", summarise_exception(exc))

        result.hits = hits
        result.detail = {
            "tool": self._tool,
            "shape": shape,
            "account": self._account,
            "from_page_index": from_index,
            "enriched": enriched,
        }
        if shape == "unrecognised":
            result.detail["warning"] = "the response could not be parsed as JSON or as a Markdown report"
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    def _to_hits(self, raw: RawToolResult, *, limit: int, debug: bool) -> tuple[list[SearchHit], str]:
        """Map a response to hits, reporting which shape it turned out to be.

        JSON is tried first even though this server has never produced any:
        it costs one branch, and it means a future version that populates
        `structuredContent` properly starts working rather than starts
        failing.
        """
        is_drive = self.key == SOURCE_GOOGLE_DRIVE

        # Only ask the payload ladder when the response could plausibly be
        # structured. Calling it unconditionally logs "unreadable payload" for
        # every single response from this server, since Markdown is its normal
        # output - a warning that would train the reader to ignore warnings.
        stripped = (raw.text or "").lstrip()
        items: list[Any] = []
        if raw.structured is not None or stripped[:1] in ("{", "["):
            items = items_from(raw.payload(source=self.key, tool=self._tool))
        if items:
            builder = drive_hit if is_drive else email_hit
            hits: list[SearchHit] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                hit = builder(item, len(hits), source_key=self.key, debug=debug)
                if hit is not None:
                    hits.append(hit)
                if len(hits) >= limit:
                    break
            if hits:
                return hits, "json"

        parser = drive_hits_from_markdown if is_drive else email_hits_from_markdown
        hits = parser(raw.text, source_key=self.key, debug=debug)[:limit]
        if hits:
            if is_drive:
                # Remembered from the same rows the hits came from, because
                # enrichment has to tell a Google Doc from a PDF and the hit
                # itself carries nothing that does.
                self._type_hints.update(drive_type_hints(raw.text))
                self._titles.update(
                    {hit.external_id: hit.title for hit in hits if hit.external_id}
                )
            return hits, "markdown"

        # A genuinely empty result reads "No messages found for query: …",
        # which is a successful search of nothing rather than a parse failure.
        if _HEADING.search(raw.text or "") or "No " in (raw.text or ""):
            return [], "markdown"
        return [], "unrecognised"

    async def _enrich(self, hits: list[SearchHit], *, query: str, ctx: SearchContext) -> int:
        """Fetch body text for hits that have none, in place.

        Search returns metadata only, so without this an answer has titles to
        cite and nothing to quote - which defeats the point of citations.

        The excerpt is centred on where the query matched rather than taken
        from the top of the document. Drive returns no highlight of any kind,
        so the alternative is showing a document's opening paragraph to
        explain a hit that was really about page five.

        One extra call per hit, which is why the caller bounds how many. Only
        Google Docs and Gmail messages: a PDF or an image has nothing to
        extract this way.

        A failure here is deliberately silent. A hit with a title and a
        working link is still useful, and losing a whole source because one
        document could not be read would be far worse.
        """
        enriched = 0
        for hit in hits:
            if not hit.external_id or hit.snippet:
                continue

            # A PDF is neither a Doc nor a message: Drive refuses to export it
            # ("Export only supports Docs Editors files") and refuses to render
            # it ("not a viewable image type"), so the only way to its content
            # is to download the file and parse it here.
            if is_pdf(self._type_hints.get(hit.external_id)):
                text = await self._read_pdf(hit.external_id, ctx=ctx, transcribe=False)
                self._schedule_transcription(hit.external_id, ctx=ctx)
                if text:
                    # A wider window than the other surfaces get. A PDF's
                    # answer is often a diagram transcription several
                    # sentences long, and 400 characters cuts it mid-pinout -
                    # while the answer prompt has room for 1200 per hit and
                    # was simply not being given it.
                    hit.snippet = excerpt_around(
                        text, query, self._settings.answer_snippet_chars
                    )
                    enriched += 1
                continue

            if hit.kind == "email":
                tool, args = "manage_email", {"operation": "read", "messageId": hit.external_id}
            elif "docs.google.com/document" in (hit.url or ""):
                tool, args = "manage_docs", {"operation": "get", "documentId": hit.external_id}
            else:
                continue

            try:
                raw = await self._call(tool, {"email": self._account, **args})
            except Exception as exc:  # noqa: BLE001
                logger.info("could not read %s: %s", hit.external_id, summarise_exception(exc))
                continue

            body = extract_report_body(raw.text)
            if body:
                hit.snippet = excerpt_around(body, query, SNIPPET_CHARS)
                enriched += 1
        return enriched

    async def _add_index_hits(
        self, hits: list[SearchHit], *, query: str, limit: int, ctx: SearchContext, debug: bool
    ) -> int:
        """Add documents found only in our own transcriptions. Returns how many.

        Appended rather than merged into the ranking: a page we transcribed is
        real evidence, but Drive's own hits matched the document's actual text
        and deserve to come first. Fusion across sources happens above this
        anyway, so this only decides the order within Drive.

        A document Drive already returned is skipped. It is the same file, and
        two cards for it would look like two documents.
        """
        matched = await page_index.search_pages(
            ctx.db, query, source_key=self.key, limit=limit
        )
        if not matched:
            return 0

        already = {hit.external_id for hit in hits if hit.external_id}
        added = 0
        for external_id, title, url, page_number, text in matched:
            if external_id in already or len(hits) >= limit:
                continue
            hits.append(
                SearchHit(
                    id=f"{self.key}:{external_id}",
                    source=self.key,
                    kind="document",
                    external_id=external_id,
                    title=title or external_id,
                    # Labelled the same way the merged document labels it, so
                    # a reader can tell a machine reading of a picture from
                    # text the document actually contains.
                    snippet=excerpt_around(
                        f"[page {page_number + 1}, read from the page image]\n{text}",
                        query,
                        self._settings.answer_snippet_chars,
                    ),
                    url=url,
                    rank_in_source=len(hits),
                    raw={"from": "page_index", "page": page_number + 1} if debug else None,
                )
            )
            already.add(external_id)
            added += 1
        return added

    async def page_images(self, hit_id: str, *, max_pages: int | None = None) -> list[bytes]:
        """The pages of a PDF hit, rendered, for the answer model to look at.

        This is the single-pass path: rather than having one model describe a
        page and another answer from the description, the page itself goes
        into the answer prompt. Nothing is transcribed, nothing is stored, and
        nothing can be lost in between - the model sees what the reader would
        see.

        Rendering is local and the file is already on the shared mount, so the
        cost is a read and a few hundred milliseconds, not a model call.
        Returns [] for anything that is not a PDF, which is most hits.
        """
        prefix, _, file_id = hit_id.partition(":")
        if prefix != self.key or not file_id:
            return []
        if not is_pdf(await self._type_of(file_id)):
            return []

        data = await self._read_pdf_bytes(file_id)
        if data is None:
            return []

        wanted = select_visual_pages(
            page_stats(data), max_pages=max_pages or self._settings.vision_max_pages
        )
        if not wanted:
            return []

        rendered = render_pages(
            data,
            wanted,
            scale=self._settings.vision_scale,
            quality=self._settings.vision_jpeg_quality,
        )
        return [rendered[index] for index in sorted(rendered)]

    async def _type_of(self, file_id: str) -> str | None:
        """The file's type, from the last search if it is still remembered and
        from the server otherwise."""
        cached = self._type_hints.get(file_id)
        if cached:
            return cached

        try:
            raw = await self._call(
                "manage_drive", {"email": self._account, "operation": "get", "fileId": file_id}
            )
        except Exception as exc:  # noqa: BLE001 - unknown type, handled by the caller
            logger.info("could not read metadata for %s: %s", file_id, summarise_exception(exc))
            return None

        found = parse_drive_type(raw.text)
        if found:
            self._type_hints[file_id] = found
        return found

    async def _read_pdf_bytes(self, file_id: str) -> bytes | None:
        """Download a PDF through the server and return its text layer.

        The bytes never cross the MCP boundary: `download` saves the file into
        the server's own workspace and reports the path, and that directory is
        bind-mounted here read-only at the same path.

        The path therefore comes from the server's own output, which is why it
        is resolved and confined to the share directory before anything is
        opened. The same mount holds the OAuth tokens, and a server that could
        name any path could name those.
        """
        from pathlib import Path

        try:
            raw = await self._call(
                "manage_drive",
                {"email": self._account, "operation": "download", "fileId": file_id},
            )
        except Exception as exc:  # noqa: BLE001 - a document that will not download is not fatal
            logger.info("could not download %s: %s", file_id, summarise_exception(exc))
            return None

        reported = parse_download_path(raw.text)
        if not reported:
            logger.info("download of %s reported no path", file_id)
            return None

        share = Path(self._settings.google_share_dir).resolve()
        try:
            path = Path(reported).resolve()
            path.relative_to(share)
        except (ValueError, OSError):
            logger.warning("refusing a download path outside %s: %r", share, reported)
            return None

        try:
            return path.read_bytes()
        except OSError as exc:
            # The usual cause is the share directory not being mounted into
            # this container at all, which is a deployment mistake rather than
            # a bad document - so it is worth a louder line than the rest.
            logger.warning("could not read %s: %s", path, exc)
            return None

    def _schedule_transcription(self, file_id: str, *, ctx: SearchContext) -> None:
        """Read the document's pages after the response has gone out.

        Not inline, and this is not a preference. A page takes ~25 seconds on
        a local 8B vision model and a datasheet has four worth reading, while
        a source that has not answered in `search_timeout_seconds` is dropped
        from the fan-out. Doing this inside the request meant the Drive source
        timed out at exactly 20000 ms and returned nothing at all - a working
        search made worse by the feature meant to improve it.

        So the first search that meets a PDF answers from its text layer and
        starts the reading; the next one has the pictures. That is what the
        index was always for - it fills in as documents are read - and the
        only thing that changes here is that the reading stops blocking.
        """
        if ctx.vision_endpoint is None or self._settings.vision_max_pages <= 0:
            return
        if file_id in self._transcribing:
            return

        self._transcribing.add(file_id)

        async def run() -> None:
            # Its own session and its own client: the request's are closed
            # when the response is sent, and this outlives the response by
            # design.
            from app.core.db import SessionLocal

            try:
                async with httpx.AsyncClient(timeout=self._settings.vision_timeout_seconds) as client, SessionLocal() as db:
                    background = SearchContext(
                        db=db,
                        user=ctx.user,
                        http_client=client,
                        vision_endpoint=ctx.vision_endpoint,
                    )
                    await self._read_pdf(file_id, ctx=background, transcribe=True)
                    await db.commit()
            except Exception as exc:  # noqa: BLE001 - nobody is waiting on this
                logger.warning("background transcription of %s failed: %s", file_id, summarise_exception(exc))
            finally:
                self._transcribing.discard(file_id)

        task = asyncio.create_task(run())
        # Held so the loop does not garbage-collect a running task, which is
        # a documented way to lose background work silently.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read_pdf(
        self, file_id: str, *, ctx: SearchContext | None = None, transcribe: bool = True
    ) -> str:
        """A PDF as text: its text layer, plus its pages read by a vision
        model when one is configured.

        The two are not alternatives. The text layer is exact and comes first;
        the page transcriptions are appended and labelled, because what is
        only in a diagram - a pin name, a wiring order, a value on a chart -
        is precisely what the text layer cannot hold. The datasheet this was
        built against extracts 6k characters of specifications and still says
        nothing about which pad is UART3 TX.

        Transcriptions are stored in `document_pages` rather than held in
        memory. They do not depend on the question, so re-describing a page
        for every search would be paying a GPU to produce a string we already
        have - and a process-local cache loses that on every deploy. Stored,
        they are also searchable, which is what makes a diagram findable at
        all: see `services/search/page_index.py`.
        """
        data = await self._read_pdf_bytes(file_id)
        if data is None:
            return ""

        text_layer = extract_pdf_text(data, limit=self._settings.google_pdf_max_chars)

        endpoint = ctx.vision_endpoint if ctx else None
        if ctx is None or endpoint is None or self._settings.vision_max_pages <= 0:
            return text_layer

        stats = page_stats(data)
        wanted = select_visual_pages(stats, max_pages=self._settings.vision_max_pages)
        if not wanted:
            return text_layer

        # Byte length identifies the version. An edited document keeps its
        # Drive id, and answering from a reading of the old one is worse than
        # not answering: nothing on the page would say it is stale.
        fingerprint = str(len(data))
        cached = await page_index.load_pages(
            ctx.db, source_key=self.key, external_id=file_id, fingerprint=fingerprint
        )
        missing = [index for index in wanted if index not in cached]

        # `transcribe=False` means "use whatever has already been read". The
        # caller is inside a request that cannot wait for a GPU.
        if missing and transcribe:
            images = render_pages(
                data,
                missing,
                scale=self._settings.vision_scale,
                quality=self._settings.vision_jpeg_quality,
            )
            described = await vision.describe_pages(
                endpoint,
                images,
                http_client=ctx.http_client,
                timeout=self._settings.vision_timeout_seconds,
                concurrency=self._settings.vision_concurrency,
            )
            # The misses are stored too. A page the model said nothing about
            # will say nothing next time either, and rendering it again on
            # every search is the expensive half.
            fresh = {index: described.get(index, "") for index in missing}
            cached.update(fresh)
            try:
                await page_index.save_pages(
                    ctx.db,
                    source_key=self.key,
                    external_id=file_id,
                    fingerprint=fingerprint,
                    title=self._titles.get(file_id, file_id),
                    url=drive_url(file_id, self._type_hints.get(file_id, "")),
                    pages=fresh,
                )
            except Exception as exc:  # noqa: BLE001 - an unindexed page is not a failed search
                logger.warning("could not index pages of %s: %s", file_id, summarise_exception(exc))

        return vision.merge(text_layer, {index: cached[index] for index in wanted if cached.get(index)})

    async def fetch_content(self, hit_id: str, *, ctx: SearchContext | None = None) -> dict[str, Any] | None:
        """A document's whole text, for the viewer.

        Reuses the same `manage_docs` read that enrichment uses - the
        difference is only that nothing is excerpted. Ids are the ones minted
        in `_to_hits` (`google_drive:<fileId>`), and anything else is refused
        rather than passed to the server.
        """
        prefix, _, file_id = hit_id.partition(":")
        if prefix != self.key or not file_id:
            return None

        if is_pdf(await self._type_of(file_id)):
            # With a context the viewer shows what the answer saw, diagrams
            # included; without one it degrades to the text layer rather than
            # refusing.
            text = await self._read_pdf(file_id, ctx=ctx)
            if not text:
                return None
            return {
                "title": file_id,
                "text": text[:MAX_CONTENT_CHARS],
                "language": None,
                "truncated": len(text) > MAX_CONTENT_CHARS,
            }

        try:
            raw = await self._call(
                "manage_docs", {"email": self._account, "operation": "get", "documentId": file_id}
            )
        except Exception as exc:  # noqa: BLE001 - reported to the caller as absent
            logger.info("could not read %s: %s", file_id, summarise_exception(exc))
            return None

        body = extract_report_body(raw.text)
        if not body:
            return None
        return {
            "title": file_id,
            "text": body[:MAX_CONTENT_CHARS],
            "language": None,
            "truncated": len(body) > MAX_CONTENT_CHARS,
        }
