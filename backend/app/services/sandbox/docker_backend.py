"""Docker-backed `SandboxBackend`. Talks to the daemon over whatever
`docker.from_env()` resolves to - on the target deployment that's the
socket the `worker` container mounts read-write (see `docker-compose.yml`
and the plan's note on that access being root-equivalent on the VPS host).

Each `start()` launches one long-lived container (entrypoint just sleeps)
that every `exec()` call for that run execs into, rather than a fresh
container per command - matching "container lives for the run + idle
timeout" from the plan. Command timeouts are enforced *inside* the
container via coreutils `timeout`, not just by giving up on the host-side
await, so a runaway process can't outlive the call that started it.
"""

import asyncio
import logging
import shlex
import time

import docker
from docker.models.containers import Container

from app.services.context.compactor import truncate_tool_output
from app.services.sandbox.base import ExecResult, SandboxBackend, SandboxConfig, SandboxHandle

logger = logging.getLogger("llmhell.sandbox")

SANDBOX_LABEL_KEY = "llmhell.sandbox"
SANDBOX_LABEL_VALUE = "true"


class DockerSandboxHandle(SandboxHandle):
    def __init__(self, container: Container) -> None:
        self._container = container
        self._last_used = time.monotonic()

    async def exec(
        self, command: list[str], *, timeout: float | None = None, max_output_lines: int = 2000
    ) -> ExecResult:
        shell_command = " ".join(shlex.quote(part) for part in command)
        if timeout:
            full_cmd = ["timeout", "--signal=KILL", str(int(timeout)), "sh", "-c", shell_command]
        else:
            full_cmd = ["sh", "-c", shell_command]

        def _run() -> tuple[int, tuple[bytes | None, bytes | None]]:
            result = self._container.exec_run(full_cmd, demux=True, workdir="/workspace")
            return result.exit_code, result.output

        exit_code, (stdout_bytes, stderr_bytes) = await asyncio.to_thread(_run)
        self._last_used = time.monotonic()

        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")

        truncated_stdout = truncate_tool_output(stdout, max_output_lines)
        truncated_stderr = truncate_tool_output(stderr, max_output_lines)

        return ExecResult(
            command=shell_command,
            exit_code=exit_code,
            stdout=truncated_stdout,
            stderr=truncated_stderr,
            timed_out=timeout is not None and exit_code in (124, 137),
            truncated=truncated_stdout != stdout or truncated_stderr != stderr,
        )

    async def stop(self) -> None:
        def _remove() -> None:
            try:
                self._container.remove(force=True)
            except docker.errors.NotFound:
                pass

        await asyncio.to_thread(_remove)

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used

    @property
    def container_id(self) -> str:
        return self._container.id


class DockerSandbox(SandboxBackend):
    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client or docker.from_env()
        self._handles: dict[str, DockerSandboxHandle] = {}

    async def start(self, config: SandboxConfig) -> SandboxHandle:
        labels = {SANDBOX_LABEL_KEY: SANDBOX_LABEL_VALUE, **(config.labels or {})}

        def _run() -> Container:
            return self._client.containers.run(
                config.image,
                command=["sleep", "infinity"],
                detach=True,
                working_dir="/workspace",
                volumes={config.workspace_host_path: {"bind": "/workspace", "mode": "rw"}},
                network_mode="bridge" if config.network_enabled else "none",
                read_only=True,
                tmpfs={"/tmp": "size=256m"},
                mem_limit=config.mem_limit,
                pids_limit=config.pids_limit,
                nano_cpus=int(config.cpus * 1_000_000_000),
                labels=labels,
            )

        container = await asyncio.to_thread(_run)
        handle = DockerSandboxHandle(container)
        self._handles[container.id] = handle
        return handle

    async def reap_idle(self, idle_timeout_seconds: float) -> int:
        stale = [handle for handle in self._handles.values() if handle.idle_seconds >= idle_timeout_seconds]
        for handle in stale:
            await handle.stop()
            self._handles.pop(handle.container_id, None)
        return len(stale)

    async def reap_orphans(self) -> int:
        def _list_and_remove() -> int:
            containers = self._client.containers.list(
                all=True, filters={"label": f"{SANDBOX_LABEL_KEY}={SANDBOX_LABEL_VALUE}"}
            )
            removed = 0
            for container in containers:
                try:
                    container.remove(force=True)
                    removed += 1
                except docker.errors.NotFound:
                    pass
                except Exception:  # noqa: BLE001 - best-effort cleanup, one bad container shouldn't block the rest
                    logger.exception("Failed to remove orphaned sandbox container %s", container.id)
            return removed

        removed = await asyncio.to_thread(_list_and_remove)
        if removed:
            logger.info("Reaped %d orphaned sandbox container(s) at startup", removed)
        return removed
