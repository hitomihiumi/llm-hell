"""Searching Google Workspace through aaronsb/google-workspace-mcp.

Three things make this the awkward source of the three:

  * **It is stdio-only.** No HTTP transport exists, so it cannot simply be a
    sidecar the API dials. It is either a child process of the API, or it is
    bridged - `docker/google-mcp/` wraps it with supergateway so it speaks
    streamable HTTP like the others. `GOOGLE_MCP_MODE` picks which, and this
    connector handles both by routing every call through `_call`.

  * **It shells out to `gws`**, Google's Workspace CLI, which is a Rust
    binary published as `@googleworkspace/cli` on npm and NOT a dependency
    of the MCP package. The image installs both.

  * **First use needs an interactive browser consent**, which cannot happen
    in a container. Tokens are produced on a workstation and mounted in; see
    docs/google-workspace-setup.md.

The tools are fat operation-dispatchers rather than one tool per action, so
searching Drive is `manage_drive {operation: "search"}`.

RESPONSE SHAPES HERE ARE UNVERIFIED. Unlike the GitLab and Postgres
connectors, this one could not be probed against a live server - that needs
OAuth credentials this repository does not have. Every field name below is
taken from the Drive and Gmail REST APIs the server wraps, and the extraction
is written as a ladder of alternatives for that reason. Run
`tools/mcp_probe.py --url http://localhost:3103/mcp call manage_drive
'{"operation":"search","query":"x"}'` once credentials exist, save the result
into tests/fixtures/mcp/, and tighten this against it.
"""

import logging
import time
from typing import Any

from app.core.config import Settings
from app.models.source import SOURCE_GOOGLE_DRIVE, SOURCE_GOOGLE_MAIL, Source
from app.schemas.search import SearchHit
from app.services.mcp.connector import SearchContext, SourceResult, parse_timestamp, truncate
from app.services.mcp.transport import (
    McpError,
    RawToolResult,
    call_tool,
    http_session,
    list_tool_names,
    summarise_exception,
)

logger = logging.getLogger("llmhell.mcp.google")

SNIPPET_CHARS = 400

# Which fat tool serves which source, and what a hit from it is.
_SURFACES: dict[str, dict[str, Any]] = {
    SOURCE_GOOGLE_DRIVE: {"tool": "manage_drive", "kind": "document"},
    SOURCE_GOOGLE_MAIL: {"tool": "manage_email", "kind": "email"},
}


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

    @property
    def _account(self) -> str | None:
        configured = (self._source.config or {}).get("account_email") if self._source else None
        return configured or self._settings.google_account_email or None

    def _base_args(self) -> dict[str, Any]:
        # The server is multi-account and routes on this. Omitted rather than
        # sent empty when unset, so it can fall back to its own default.
        return {"email": self._account} if self._account else {}

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
            report["warning"] = (
                "GOOGLE_ACCOUNT_EMAIL is not set, so the server will use whichever account "
                "it considers default"
            )

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

        arguments = {**self._base_args(), "operation": "search", "query": query}
        # Both tools take a page size, under different names depending on
        # which API they front; sending both is harmless and saves guessing.
        arguments["pageSize"] = limit
        arguments["maxResults"] = limit

        try:
            raw = await self._call(self._tool, arguments)
        except Exception as exc:  # noqa: BLE001 - isolation is the contract
            logger.warning("%s search failed: %s", self.key, summarise_exception(exc))
            result.error = summarise_exception(exc)
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        payload = raw.payload(source=self.key, tool=self._tool)
        items = items_from(payload)
        builder = drive_hit if self.key == SOURCE_GOOGLE_DRIVE else email_hit

        hits: list[SearchHit] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            hit = builder(item, len(hits), source_key=self.key, debug=ctx.debug)
            if hit is not None:
                hits.append(hit)
            if len(hits) >= limit:
                break

        result.hits = hits
        result.detail = {"tool": self._tool, "items": len(items), "account": self._account}
        if items and not hits:
            # Something came back and none of it was recognisable - the most
            # likely symptom of the unverified shapes above.
            result.detail["warning"] = "results were returned but none could be mapped"
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
