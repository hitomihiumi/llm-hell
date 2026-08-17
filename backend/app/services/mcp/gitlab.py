"""Searching GitLab through zereight/gitlab-mcp.

Everything here is shaped by what a live GitLab CE actually did, not by the
README (docs/mcp-spike-findings.md):

  * **`search_code` does not work on Community Edition.** Instance-wide code
    search is GitLab *advanced search*, which is Elasticsearch-backed and
    Premium/Ultimate only; on CE the API returns
    `400 scope does not have a valid value`. `search_project_code` works
    fine, so the connector searches project by project and treats
    instance-wide search as an optimisation to try first, not a requirement.

  * **The code-search tools are not enabled by default.** A stock container
    exposes 63 tools, none of which searches code - the `search` toolset
    reports `active: false`. `GITLAB_TOOLSETS` in docker-compose.yml turns it
    on.

  * **Code hits carry no `web_url`.** They have `path`, `ref`, `startline`
    and `project_id`, so the permalink is synthesised - which needs a
    project-id-to-path map that only the project tools provide.

  * **`web_url` is written with the instance's configured external host**,
    which for a container-hosted GitLab is something like
    `http://aea717aec055/...` - unreachable from a browser. Every URL is
    rewritten onto `settings.gitlab_web_url`.
"""

import logging
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import Settings
from app.models.source import Source
from app.schemas.search import SearchHit
from app.services.mcp.connector import SearchContext, SourceResult, parse_timestamp, truncate
from app.services.mcp.transport import (
    McpError,
    call_tool,
    http_session,
    list_tool_names,
    summarise_exception,
)

logger = logging.getLogger("llmhell.mcp.gitlab")

SNIPPET_CHARS = 400
# How many projects to search when none are configured. Each one is a
# separate API round-trip, so this is a latency budget, not a limit on how
# much GitLab can hold.
MAX_DISCOVERED_PROJECTS = 10


class GitLabConnector:
    kind = "gitlab"

    def __init__(self, source: Source | None, settings: Settings, *, key: str = "gitlab"):
        self.key = key
        self._settings = settings
        self._source = source
        # project id -> path_with_namespace, for permalink synthesis.
        self._project_paths: dict[str, str] = {}
        # None until probed. False once instance-wide search has been seen to
        # fail, so a CE instance is not asked again on every single search.
        self._instance_search_works: bool | None = None

    # --- configuration ----------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        token = self._settings.gitlab_mcp_auth_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    @property
    def _configured_projects(self) -> list[str]:
        configured = (self._source.config or {}).get("project_ids") if self._source else None
        return [str(p) for p in (configured or self._settings.gitlab_default_project_ids)]

    def _rewrite_host(self, url: str | None) -> str | None:
        """Put a GitLab-supplied URL back onto a host a browser can reach.

        GitLab builds `web_url` from its own `external_url` setting. For an
        instance running in a container that is the container hostname, so
        the links it returns are correct for the server and useless for the
        user looking at the results.
        """
        if not url:
            return None
        base = self._settings.gitlab_web_url
        if not base:
            return url
        target, source_parts = urlsplit(base), urlsplit(url)
        return urlunsplit(
            (target.scheme or source_parts.scheme, target.netloc, source_parts.path, source_parts.query, source_parts.fragment)
        )

    # --- health -----------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        try:
            async with http_session(self._settings.gitlab_mcp_url, self._headers) as session:
                tools = await list_tool_names(session, timeout=self._settings.mcp_init_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return {"ok": False, "error": summarise_exception(exc), "url": self._settings.gitlab_mcp_url}

        report = {
            "ok": True,
            "url": self._settings.gitlab_mcp_url,
            "tools": tools,
            "has_project_code_search": "search_project_code" in tools,
            "has_instance_code_search": "search_code" in tools,
            "configured_projects": self._configured_projects,
        }
        if not report["has_project_code_search"]:
            # The single most likely misconfiguration, and it is silent:
            # the server starts happily and simply has no search tools.
            report["warning"] = (
                "the `search` toolset is not enabled on this server - set "
                "GITLAB_TOOLSETS to include `search`"
            )
        return report

    # --- search -----------------------------------------------------------

    async def search(self, query: str, *, limit: int, ctx: SearchContext) -> SourceResult:
        started = time.monotonic()
        result = SourceResult(source_key=self.key)

        try:
            async with http_session(self._settings.gitlab_mcp_url, self._headers) as session:
                repo_hits, repo_error = await self._search_repositories(session, query, limit=limit, ctx=ctx)
                code_hits, code_error, mode = await self._search_code(session, query, limit=limit, ctx=ctx)
        except Exception as exc:  # noqa: BLE001 - isolation is the contract
            logger.warning("gitlab search failed: %s", summarise_exception(exc))
            result.error = summarise_exception(exc)
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        # Code first: a matching line is far more specific than a project
        # whose name happens to contain the search term.
        hits = code_hits + repo_hits
        for rank, hit in enumerate(hits):
            hit.rank_in_source = rank
        result.hits = hits[:limit]

        errors = [error for error in (code_error, repo_error) if error]
        if errors and not hits:
            result.error = "; ".join(errors)
        elif errors:
            result.degraded = True

        result.detail = {
            "code_mode": mode,
            "code_hits": len(code_hits),
            "repository_hits": len(repo_hits),
            "errors": errors,
        }
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    async def _search_repositories(
        self, session, query: str, *, limit: int, ctx: SearchContext
    ) -> tuple[list[SearchHit], str | None]:
        try:
            raw = await call_tool(
                session,
                "search_repositories",
                {"search": query},
                timeout=self._settings.mcp_call_timeout_seconds,
            )
        except McpError as exc:
            return [], f"search_repositories: {exc}"

        items = self._items(raw.payload(source=self.key, tool="search_repositories"))
        hits: list[SearchHit] = []
        for project in items:
            if not isinstance(project, dict):
                continue
            project_id = str(project.get("id") or "")
            path = project.get("path_with_namespace") or project.get("name") or project_id
            if project_id and path:
                # Cache for permalink synthesis, which code hits need.
                self._project_paths[project_id] = str(path)
            hits.append(
                SearchHit(
                    id=f"{self.key}:project:{project_id or path}",
                    source=self.key,
                    kind="repository",
                    external_id=project_id or None,
                    title=str(path),
                    snippet=truncate(project.get("description") or "", SNIPPET_CHARS),
                    url=self._rewrite_host(project.get("web_url")),
                    timestamp=parse_timestamp(project.get("last_activity_at")),
                    raw=project if ctx.debug else None,
                )
            )
        return hits[:limit], None

    async def _search_code(
        self, session, query: str, *, limit: int, ctx: SearchContext
    ) -> tuple[list[SearchHit], str | None, str]:
        """Instance-wide when the instance supports it, per-project otherwise."""
        if self._instance_search_works is not False:
            try:
                raw = await call_tool(
                    session, "search_code", {"search": query}, timeout=self._settings.mcp_call_timeout_seconds
                )
            except McpError as exc:
                # CE answers `400 scope does not have a valid value`. Record
                # it so every later search goes straight to per-project.
                logger.info("instance-wide code search unavailable, using per-project: %s", exc)
                self._instance_search_works = False
            else:
                self._instance_search_works = True
                blobs = self._items(raw.payload(source=self.key, tool="search_code"))
                return await self._blobs_to_hits(session, blobs, ctx=ctx), None, "instance"

        project_ids = self._configured_projects or await self._discover_projects(session)
        if not project_ids:
            return [], "no projects to search (set GITLAB_DEFAULT_PROJECT_IDS)", "per_project"

        async def one(project_id: str):
            return await call_tool(
                session,
                "search_project_code",
                {"project_id": project_id, "search": query},
                timeout=self._settings.mcp_call_timeout_seconds,
            )

        # Sequential rather than gathered: these share one MCP session, and
        # the session is not safe to drive concurrently.
        blobs: list[dict[str, Any]] = []
        errors: list[str] = []
        for project_id in project_ids[:MAX_DISCOVERED_PROJECTS]:
            try:
                raw = await one(project_id)
            except McpError as exc:
                errors.append(f"project {project_id}: {exc}")
                continue
            blobs.extend(self._items(raw.payload(source=self.key, tool="search_project_code")))
            if len(blobs) >= limit:
                break

        hits = await self._blobs_to_hits(session, blobs, ctx=ctx)
        return hits, ("; ".join(errors) if errors and not hits else None), "per_project"

    async def _discover_projects(self, session) -> list[str]:
        try:
            raw = await call_tool(
                session, "list_projects", {}, timeout=self._settings.mcp_call_timeout_seconds
            )
        except McpError as exc:
            logger.info("could not list projects: %s", exc)
            return []

        ids: list[str] = []
        for project in self._items(raw.payload(source=self.key, tool="list_projects")):
            if not isinstance(project, dict):
                continue
            project_id = str(project.get("id") or "")
            if not project_id:
                continue
            ids.append(project_id)
            path = project.get("path_with_namespace")
            if path:
                self._project_paths[project_id] = str(path)
        return ids

    async def _blobs_to_hits(self, session, blobs: list[Any], *, ctx: SearchContext) -> list[SearchHit]:
        await self._ensure_project_paths(session, blobs)

        hits: list[SearchHit] = []
        for blob in blobs:
            if not isinstance(blob, dict):
                continue
            path = blob.get("path") or blob.get("filename")
            if not path:
                continue
            project_id = str(blob.get("project_id") or "")
            startline = blob.get("startline") or 1
            hits.append(
                SearchHit(
                    id=f"{self.key}:code:{project_id}:{path}:{startline}",
                    source=self.key,
                    kind="code",
                    external_id=f"{project_id}:{path}",
                    title=f"{self._project_paths.get(project_id, project_id)}/{path}",
                    snippet=truncate(blob.get("data") or "", SNIPPET_CHARS),
                    url=self._blob_url(project_id, str(path), blob.get("ref") or "HEAD", startline),
                    raw=blob if ctx.debug else None,
                )
            )
        return hits

    async def _ensure_project_paths(self, session, blobs: list[Any]) -> None:
        """Resolve any project id a blob referenced but we have no path for.

        Without the path there is no permalink, only a bare id - so this is
        worth a round-trip, but only for ids actually seen in results.
        """
        missing = {
            str(blob.get("project_id"))
            for blob in blobs
            if isinstance(blob, dict) and blob.get("project_id") and str(blob["project_id"]) not in self._project_paths
        }
        for project_id in missing:
            try:
                raw = await call_tool(
                    session,
                    "get_project",
                    {"project_id": project_id},
                    timeout=self._settings.mcp_call_timeout_seconds,
                )
            except McpError as exc:
                logger.info("could not resolve project %s: %s", project_id, exc)
                continue
            payload = raw.payload(source=self.key, tool="get_project")
            if isinstance(payload, dict) and payload.get("path_with_namespace"):
                self._project_paths[project_id] = str(payload["path_with_namespace"])

    def _blob_url(self, project_id: str, path: str, ref: str, startline: Any) -> str | None:
        base = self._settings.gitlab_web_url.rstrip("/")
        project_path = self._project_paths.get(project_id)
        if not base or not project_path:
            return None
        anchor = f"#L{startline}" if startline else ""
        return f"{base}/{project_path}/-/blob/{ref}/{path}{anchor}"

    @staticmethod
    def _items(payload: Any) -> list[Any]:
        """Unwrap a tool result into a list of items.

        `search_project_code` returns a bare JSON array, while
        `search_repositories` returns `{"count": n, "items": [...]}` - so
        neither shape can be assumed.
        """
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("items", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []
