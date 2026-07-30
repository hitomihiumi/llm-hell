import pytest

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.services.context.assembler import PinnedFile, assemble_context
from app.services.llm.toolcalls import ToolSpec
from app.services.projects.tree import FileEntry

FILES = [
    FileEntry(id="1", path="src/main.py", size_bytes=100, token_count=40, pinned=True),
    FileEntry(id="2", path="README.md", size_bytes=30, token_count=10, pinned=False),
]


def _endpoint(tools_mode: str = "json_protocol") -> ModelEndpoint:
    return ModelEndpoint(
        id="ep-1",
        name="test",
        base_url="http://mock-vllm/v1",
        model_id="glm-4.7",
        role="executor",
        ctx_window=32768,
        tools_mode=tools_mode,
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )


@pytest.mark.asyncio
async def test_assemble_context_minimal_has_system_and_repo_map_only(mock_vllm_http_client) -> None:
    result = await assemble_context(
        role="planner",
        all_files=FILES,
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[{"role": "user", "content": "fix the bug"}],
        tool_specs=None,
        endpoint=_endpoint(),
        http_client=mock_vllm_http_client,
    )

    roles_and_content = [(m["role"], m["content"]) for m in result.messages]
    assert roles_and_content[0][0] == "system"
    assert "planning half" in roles_and_content[0][1]
    assert any("2 files" in c for _, c in roles_and_content if "files" in c)
    assert roles_and_content[-1] == ("user", "fix the bug")

    # no pinned/retrieved/summary text -> no extra system messages for them
    system_messages = [c for r, c in roles_and_content if r == "system"]
    assert len(system_messages) == 2  # system prompt + repo_map only


@pytest.mark.asyncio
async def test_assemble_context_includes_pinned_and_retrieved(mock_vllm_http_client) -> None:
    result = await assemble_context(
        role="executor",
        all_files=FILES,
        pinned_files=[PinnedFile(entry=FILES[0], content="print('hi')")],
        retrieved_files={"README.md": "# demo"},
        summary="Earlier: renamed a function.",
        history=[{"role": "user", "content": "continue"}],
        tool_specs=None,
        endpoint=_endpoint(),
        http_client=mock_vllm_http_client,
    )

    contents = [m["content"] for m in result.messages]
    assert any("print('hi')" in c for c in contents)
    assert any("# demo" in c for c in contents)
    assert any("renamed a function" in c for c in contents)
    assert result.usage.segments["pinned"] > 0
    assert result.usage.segments["retrieved"] > 0
    assert result.usage.segments["summary"] > 0


@pytest.mark.asyncio
async def test_assemble_context_json_protocol_tools_in_system_prompt(mock_vllm_http_client) -> None:
    tool = ToolSpec(name="read_file", description="Read a file.", parameters={"type": "object", "properties": {}})
    result = await assemble_context(
        role="executor",
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        tool_specs=[tool],
        endpoint=_endpoint(tools_mode="json_protocol"),
        http_client=mock_vllm_http_client,
    )

    system_text = result.messages[0]["content"]
    assert "read_file" in system_text
    assert "```tool_call" in system_text


@pytest.mark.asyncio
async def test_assemble_context_native_tools_mode_excludes_tool_prompt(mock_vllm_http_client) -> None:
    tool = ToolSpec(name="read_file", description="Read a file.", parameters={"type": "object", "properties": {}})
    result = await assemble_context(
        role="executor",
        all_files=[],
        pinned_files=[],
        retrieved_files={},
        summary=None,
        history=[],
        tool_specs=[tool],
        endpoint=_endpoint(tools_mode="native"),
        http_client=mock_vllm_http_client,
    )

    system_text = result.messages[0]["content"]
    assert "tool_call" not in system_text
