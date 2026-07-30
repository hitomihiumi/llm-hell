"""The sandbox abstraction the agent loop (task 6) runs shell commands
through. Only command execution goes through here - reading/writing files
in a project's workspace is plain host filesystem I/O (see
`services.projects.ingest`/`diff`), since the sandbox container mounts
that same directory; isolation only matters for *running* things.

Kept as an ABC (rather than calling `docker_backend` directly) so the
agent loop and its tests aren't hardwired to Docker - a future
`sandboxd`-backed implementation (see the plan's note on the worker's
`/var/run/docker.sock` access) is a new class behind this interface, not a
rewrite of every caller.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import PurePosixPath


def workspace_host_path(project_id: str, projects_host_dir: str) -> str:
    """The *host* filesystem path to a project's workspace, for use as a
    bind-mount source when asking the host Docker daemon to create a
    sandbox container - not the in-container path the api/worker
    processes use for normal file I/O (`Settings.projects_dir`). See the
    docker-compose.yml comment on the worker's volume mount for why these
    two have to be kept separate."""
    return str(PurePosixPath(projects_host_dir) / project_id / "workspace")


@dataclass(frozen=True)
class SandboxConfig:
    image: str
    workspace_host_path: str
    cpus: float
    mem_limit: str
    pids_limit: int
    command_timeout_seconds: int
    network_enabled: bool = False
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class ExecResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


class SandboxHandle(ABC):
    """A single long-lived container tied to one run, executed into
    repeatedly rather than recreated per command."""

    @abstractmethod
    async def exec(
        self, command: list[str], *, timeout: float | None = None, max_output_lines: int = 2000
    ) -> ExecResult: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @property
    @abstractmethod
    def idle_seconds(self) -> float:
        """Time since this handle's last `exec()` call returned."""
        ...


class SandboxBackend(ABC):
    @abstractmethod
    async def start(self, config: SandboxConfig) -> SandboxHandle: ...

    @abstractmethod
    async def reap_idle(self, idle_timeout_seconds: float) -> int:
        """Stops handles this backend started that have been idle past the
        timeout. Called periodically by the worker, not per-run."""
        ...

    @abstractmethod
    async def reap_orphans(self) -> int:
        """Removes containers left over from a previous worker process
        (crash, restart) by label, independent of this instance's own
        in-memory bookkeeping. Called once on worker startup."""
        ...
