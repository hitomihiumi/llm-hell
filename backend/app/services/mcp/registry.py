"""Owns the connector instances and the one piece of real lifecycle in this
subsystem: the optional Google stdio subprocess.

**Why HTTP connectors do not pool sessions.** An MCP session is stateful -
it carries an `initialize` handshake and, over streamable HTTP, a session
id. Pooling means owning invalidation and reconnection, and with per-user
GitLab tokens a shared session would be actively wrong. So `http_session` /
`sse_session` are entered per search. That costs one extra round-trip
against an operation that already waits on a live API and an LLM, which is
a trade worth making for not having to reason about a stale shared session.

**Why stdio needs a supervisor task.** `stdio_client` opens an anyio task
group, and anyio requires the `async with` to be entered and exited in the
same task - so the obvious "hold the session in a module global" approach
raises a cancel-scope error the first time anything closes it. The way out
is an actor: one long-lived task owns the session for its whole lifetime
and is the only thing that ever touches it; request handlers post work to a
queue and await a future.

Spawning per request is not an option either - `npx` cold start plus the
`gws` binary's own initialisation is seconds, on every search.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.services.mcp.connector import Connector
from app.services.mcp.transport import McpError, RawToolResult, call_tool, stdio_session

logger = logging.getLogger("llmhell.mcp.registry")

# Backoff bounds for restarting a crashed stdio server.
_RESTART_DELAY_MIN = 1.0
_RESTART_DELAY_MAX = 30.0


@dataclass
class _StdioRequest:
    tool: str
    arguments: dict[str, Any]
    timeout: float
    text_error_prefixes: tuple[str, ...]
    future: asyncio.Future


class StdioSupervisor:
    """Serialises tool calls onto a single long-lived child process.

    Only `_run` ever touches the ClientSession, and it both opens and closes
    it - see the module docstring for why that matters.
    """

    def __init__(self, command: str, args: list[str], env: dict[str, str], *, name: str = "stdio"):
        self._command, self._args, self._env, self._name = command, args, env, name
        self._queue: asyncio.Queue[_StdioRequest] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"mcp-{self._name}-supervisor")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def call(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout: float,
        text_error_prefixes: tuple[str, ...] = (),
    ) -> RawToolResult:
        if self._task is None:
            raise McpError(f"{self._name} supervisor is not running")
        request = _StdioRequest(
            tool=tool,
            arguments=arguments,
            timeout=timeout,
            text_error_prefixes=text_error_prefixes,
            future=asyncio.get_running_loop().create_future(),
        )
        await self._queue.put(request)
        # The outer bound covers time spent queued behind other calls, which
        # the inner per-call timeout does not see.
        return await asyncio.wait_for(request.future, timeout=timeout * 2)

    async def _run(self) -> None:
        delay = _RESTART_DELAY_MIN
        while True:
            try:
                async with stdio_session(self._command, self._args, self._env) as session:
                    logger.info("%s MCP server started", self._name)
                    delay = _RESTART_DELAY_MIN
                    self._ready.set()
                    while True:
                        request = await self._queue.get()
                        if request.future.done():  # caller already timed out
                            continue
                        try:
                            result = await call_tool(
                                session,
                                request.tool,
                                request.arguments,
                                timeout=request.timeout,
                                text_error_prefixes=request.text_error_prefixes,
                            )
                            request.future.set_result(result)
                        except Exception as exc:  # noqa: BLE001 - relayed to the caller
                            request.future.set_exception(exc)
                            # A tool-level failure is the caller's problem,
                            # not the session's - keep serving. A transport
                            # failure will surface as the loop exiting.
            except asyncio.CancelledError:
                logger.info("%s MCP supervisor shutting down", self._name)
                raise
            except Exception as exc:  # noqa: BLE001 - restart loop
                logger.warning("%s MCP server died (%s); restarting in %.0fs", self._name, exc, delay)
            finally:
                self._ready.clear()
                # Anything still queued or in flight cannot be served by a
                # session that no longer exists.
                self._fail_pending(McpError(f"{self._name} MCP server is unavailable"))

            await asyncio.sleep(delay)
            delay = min(delay * 2, _RESTART_DELAY_MAX)

    def _fail_pending(self, exc: Exception) -> None:
        while not self._queue.empty():
            try:
                request = self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - race with put()
                break
            if not request.future.done():
                request.future.set_exception(exc)


class McpRegistry:
    """Holds the connectors and the stdio supervisor, if there is one."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient):
        self._settings = settings
        self._http_client = http_client
        self._connectors: dict[str, Connector] = {}
        self._google_supervisor: StdioSupervisor | None = None

    def register(self, connector: Connector) -> None:
        self._connectors[connector.key] = connector

    async def start(self) -> None:
        settings = self._settings
        if settings.google_mcp_mode != "stdio":
            return

        # N uvicorn workers would mean N child processes all refreshing and
        # rewriting the same OAuth token file, which can race and invalidate
        # the refresh token. There is no locking to add here - the answer is
        # the HTTP bridge sidecar, which is the default mode.
        if os.environ.get("WEB_CONCURRENCY") not in (None, "", "1"):
            logger.warning(
                "google_mcp_mode=stdio with WEB_CONCURRENCY=%s: each worker will spawn its own "
                "Google MCP server and they will fight over the same OAuth token file. "
                "Use google_mcp_mode=http (the google-mcp sidecar) to scale out.",
                os.environ["WEB_CONCURRENCY"],
            )

        env = dict(os.environ)
        if settings.google_client_id:
            env["GOOGLE_CLIENT_ID"] = settings.google_client_id
        if settings.google_client_secret:
            env["GOOGLE_CLIENT_SECRET"] = settings.google_client_secret

        self._google_supervisor = StdioSupervisor(
            settings.google_mcp_command, list(settings.google_mcp_args), env, name="google"
        )
        await self._google_supervisor.start()

    async def aclose(self) -> None:
        if self._google_supervisor is not None:
            await self._google_supervisor.aclose()
            self._google_supervisor = None

    @property
    def google_supervisor(self) -> StdioSupervisor | None:
        return self._google_supervisor

    def get(self, key: str) -> Connector | None:
        return self._connectors.get(key)

    def connectors(self, keys: list[str] | None = None) -> list[Connector]:
        if keys is None:
            return list(self._connectors.values())
        return [self._connectors[key] for key in keys if key in self._connectors]


_registry: McpRegistry | None = None


def get_mcp_registry() -> McpRegistry:
    """Process-wide registry, exposed as a FastAPI dependency.

    Same shape as `app.api.openai_proxy.get_http_client` - a lazily-created
    module singleton behind a plain callable - so a test can swap the whole
    thing out with
    `app.dependency_overrides[get_mcp_registry] = lambda: FakeRegistry()`.
    """
    global _registry
    if _registry is None:
        from app.api.openai_proxy import get_http_client
        from app.services.mcp.builder import build_connectors

        settings = get_settings()
        _registry = McpRegistry(settings, get_http_client())
        for connector in build_connectors(settings):
            _registry.register(connector)
    return _registry


async def close_mcp_registry() -> None:
    global _registry
    if _registry is not None:
        await _registry.aclose()
        _registry = None
