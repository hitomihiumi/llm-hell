from types import SimpleNamespace

import docker.errors
import pytest

from app.services.sandbox.base import SandboxConfig
from app.services.sandbox.docker_backend import DockerSandbox, DockerSandboxHandle


class FakeContainer:
    def __init__(self, container_id: str = "fake-container-id"):
        self.id = container_id
        self.exec_calls: list[dict] = []
        self.removed = False
        self.next_result: tuple[int, tuple[bytes, bytes]] = (0, (b"ok\n", b""))

    def exec_run(self, cmd, demux=True, workdir=None):
        self.exec_calls.append({"cmd": cmd, "demux": demux, "workdir": workdir})
        exit_code, output = self.next_result
        return SimpleNamespace(exit_code=exit_code, output=output)

    def remove(self, force=True):
        self.removed = True


class FakeContainerCollection:
    def __init__(self, container: FakeContainer):
        self.run_calls: list[dict] = []
        self._container = container
        self.list_filters = None
        self.list_result: list[FakeContainer] = []

    def run(self, image, **kwargs):
        self.run_calls.append({"image": image, **kwargs})
        return self._container

    def list(self, all=True, filters=None):
        self.list_filters = filters
        return self.list_result


class FakeDockerClient:
    def __init__(self, container: FakeContainer | None = None):
        self.containers = FakeContainerCollection(container or FakeContainer())


# --- DockerSandboxHandle.exec() ---


@pytest.mark.asyncio
async def test_exec_wraps_command_with_timeout_when_given() -> None:
    container = FakeContainer()
    handle = DockerSandboxHandle(container)

    await handle.exec(["pytest", "-x"], timeout=30)

    cmd = container.exec_calls[0]["cmd"]
    assert cmd[:3] == ["timeout", "--signal=KILL", "30"]
    assert cmd[-2:] == ["sh", "-c"] or cmd[3:5] == ["sh", "-c"]
    assert "pytest -x" in cmd[-1]


@pytest.mark.asyncio
async def test_exec_skips_timeout_wrapper_when_none() -> None:
    container = FakeContainer()
    handle = DockerSandboxHandle(container)

    await handle.exec(["echo", "hi"])

    cmd = container.exec_calls[0]["cmd"]
    assert cmd[0] == "sh"
    assert "echo hi" in cmd[-1]


@pytest.mark.asyncio
async def test_exec_shell_quotes_arguments_with_spaces() -> None:
    container = FakeContainer()
    handle = DockerSandboxHandle(container)

    await handle.exec(["echo", "hello world"])

    shell_str = container.exec_calls[0]["cmd"][-1]
    assert "'hello world'" in shell_str


@pytest.mark.asyncio
async def test_exec_returns_decoded_stdout_stderr_and_exit_code() -> None:
    container = FakeContainer()
    container.next_result = (1, (b"stdout text\n", b"stderr text\n"))
    handle = DockerSandboxHandle(container)

    result = await handle.exec(["false"])

    assert result.exit_code == 1
    assert result.stdout == "stdout text\n"
    assert result.stderr == "stderr text\n"
    assert result.command == "false"


@pytest.mark.asyncio
async def test_exec_marks_timed_out_only_when_timeout_was_requested() -> None:
    container = FakeContainer()
    container.next_result = (137, (b"", b""))
    handle = DockerSandboxHandle(container)

    with_timeout = await handle.exec(["sleep", "999"], timeout=5)
    assert with_timeout.timed_out is True

    container.next_result = (137, (b"", b""))
    without_timeout = await handle.exec(["sleep", "1"])
    assert without_timeout.timed_out is False


@pytest.mark.asyncio
async def test_exec_truncates_long_output() -> None:
    container = FakeContainer()
    long_output = "\n".join(f"line {i}" for i in range(5000)).encode()
    container.next_result = (0, (long_output, b""))
    handle = DockerSandboxHandle(container)

    result = await handle.exec(["find", "."], max_output_lines=200)

    assert result.truncated is True
    assert "omitted" in result.stdout


@pytest.mark.asyncio
async def test_exec_updates_idle_seconds(monkeypatch) -> None:
    container = FakeContainer()
    handle = DockerSandboxHandle(container)

    clock = {"t": 100.0}
    monkeypatch.setattr("app.services.sandbox.docker_backend.time.monotonic", lambda: clock["t"])
    handle._last_used = clock["t"]

    clock["t"] = 130.0
    assert handle.idle_seconds == pytest.approx(30.0)

    await handle.exec(["echo", "hi"])
    assert handle.idle_seconds == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_stop_removes_container() -> None:
    container = FakeContainer()
    handle = DockerSandboxHandle(container)
    await handle.stop()
    assert container.removed is True


@pytest.mark.asyncio
async def test_stop_is_safe_when_container_already_gone() -> None:
    container = FakeContainer()

    def _raise(force=True):
        raise docker.errors.NotFound("no such container")

    container.remove = _raise
    handle = DockerSandboxHandle(container)
    await handle.stop()  # must not raise


# --- DockerSandbox ---


@pytest.mark.asyncio
async def test_start_passes_expected_container_kwargs() -> None:
    fake_client = FakeDockerClient()
    backend = DockerSandbox(client=fake_client)

    config = SandboxConfig(
        image="llmhell-runner:latest",
        workspace_host_path="/srv/projects/p1/workspace",
        cpus=1.5,
        mem_limit="2g",
        pids_limit=256,
        command_timeout_seconds=120,
        network_enabled=False,
        labels={"llmhell.run_id": "run-1"},
    )
    await backend.start(config)

    call = fake_client.containers.run_calls[0]
    assert call["image"] == "llmhell-runner:latest"
    assert call["command"] == ["sleep", "infinity"]
    assert call["network_mode"] == "none"
    assert call["read_only"] is True
    assert call["tmpfs"] == {"/tmp": "size=256m"}
    assert call["mem_limit"] == "2g"
    assert call["pids_limit"] == 256
    assert call["nano_cpus"] == 1_500_000_000
    assert call["volumes"] == {"/srv/projects/p1/workspace": {"bind": "/workspace", "mode": "rw"}}
    assert call["labels"]["llmhell.sandbox"] == "true"
    assert call["labels"]["llmhell.run_id"] == "run-1"


@pytest.mark.asyncio
async def test_start_enables_network_when_requested() -> None:
    fake_client = FakeDockerClient()
    backend = DockerSandbox(client=fake_client)

    config = SandboxConfig(
        image="llmhell-runner:latest",
        workspace_host_path="/srv/projects/p1/workspace",
        cpus=1,
        mem_limit="1g",
        pids_limit=128,
        command_timeout_seconds=60,
        network_enabled=True,
    )
    await backend.start(config)

    assert fake_client.containers.run_calls[0]["network_mode"] == "bridge"


@pytest.mark.asyncio
async def test_reap_idle_stops_only_stale_handles(monkeypatch) -> None:
    idle_container = FakeContainer("idle")
    fresh_container = FakeContainer("fresh")
    fake_client = FakeDockerClient()
    backend = DockerSandbox(client=fake_client)

    clock = {"t": 0.0}
    monkeypatch.setattr("app.services.sandbox.docker_backend.time.monotonic", lambda: clock["t"])

    idle_handle = DockerSandboxHandle(idle_container)
    fresh_handle = DockerSandboxHandle(fresh_container)
    backend._handles = {"idle": idle_handle, "fresh": fresh_handle}

    clock["t"] = 1000.0  # idle_handle's last_used was 0, fresh_handle's was also 0 at creation...
    fresh_handle._last_used = 999.0  # simulate fresh activity just before the check

    removed = await backend.reap_idle(idle_timeout_seconds=500)

    assert removed == 1
    assert idle_container.removed is True
    assert fresh_container.removed is False
    assert "idle" not in backend._handles
    assert "fresh" in backend._handles


@pytest.mark.asyncio
async def test_reap_orphans_filters_by_label_and_counts_successes() -> None:
    ok_container = FakeContainer("ok")
    broken_container = FakeContainer("broken")

    def _raise(force=True):
        raise docker.errors.NotFound("gone")

    broken_container.remove = _raise

    fake_client = FakeDockerClient()
    fake_client.containers.list_result = [ok_container, broken_container]
    backend = DockerSandbox(client=fake_client)

    removed = await backend.reap_orphans()

    assert removed == 1
    assert ok_container.removed is True
    assert fake_client.containers.list_filters == {"label": "llmhell.sandbox=true"}
