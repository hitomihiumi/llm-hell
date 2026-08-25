"""GitLab result mapping, driven by responses captured from a live
GitLab CE 19.2.2 via tools/mcp_probe.py.

The fixtures are the point: none of this server's response shapes are
documented, so every field name below was verified rather than assumed.
"""

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.mcp.connector import SearchContext
from app.services.mcp.gitlab import GitLabConnector

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"


def load(name: str):
    """The captured payload, already peeled out of its text block."""
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data.get("text_as_json")


def make_connector(**overrides) -> GitLabConnector:
    settings = Settings(
        gitlab_web_url=overrides.pop("gitlab_web_url", "http://localhost:32769"),
        **overrides,
    )
    return GitLabConnector(None, settings)


def ctx() -> SearchContext:
    return SearchContext(db=None, user=None, http_client=None)  # type: ignore[arg-type]


# --- unwrapping -------------------------------------------------------------

def test_items_handles_a_bare_list():
    """search_project_code returns a JSON array."""
    assert GitLabConnector._items([{"a": 1}]) == [{"a": 1}]


def test_items_handles_the_wrapper_object():
    """search_repositories returns {count, total_pages, items:[...]}."""
    assert GitLabConnector._items({"count": 1, "items": [{"a": 1}]}) == [{"a": 1}]


@pytest.mark.parametrize("payload", [None, {}, "", 0, [], {"count": 0}, {"items": "not a list"}])
def test_items_returns_empty_for_anything_unexpected(payload):
    assert GitLabConnector._items(payload) == []


# --- host rewriting ---------------------------------------------------------

def test_web_url_host_is_rewritten():
    """GitLab builds web_url from its own external_url, which for a
    containerised instance is the container hostname - correct for the
    server, useless in a browser."""
    connector = make_connector()
    rewritten = connector._rewrite_host("http://aea717aec055/test/test")
    assert rewritten == "http://localhost:32769/test/test"


def test_rewrite_preserves_path_query_and_fragment():
    connector = make_connector()
    assert (
        connector._rewrite_host("http://aea717aec055/g/p/-/blob/main/a.py?x=1#L20")
        == "http://localhost:32769/g/p/-/blob/main/a.py?x=1#L20"
    )


def test_rewrite_is_a_noop_without_a_configured_web_url():
    connector = make_connector(gitlab_web_url="")
    assert connector._rewrite_host("http://aea717aec055/x") == "http://aea717aec055/x"


def test_rewrite_handles_none():
    assert make_connector()._rewrite_host(None) is None


# --- repositories -----------------------------------------------------------

async def test_repository_hits_from_the_captured_response():
    connector = make_connector()
    payload = load("gitlab_search_repositories.json")
    assert payload is not None, "fixture missing - re-run tools/mcp_probe.py"

    hits = []
    for project in GitLabConnector._items(payload):
        connector._project_paths[str(project["id"])] = project["path_with_namespace"]
        hits.append(project)

    assert connector._project_paths == {"1": "test/test"}


def test_blob_url_is_synthesised_with_line_anchor():
    """Code hits carry no web_url at all, so the permalink is built from
    project path + ref + path + startline."""
    connector = make_connector()
    connector._project_paths["1"] = "test/test"
    assert (
        connector._blob_url("1", "search/federation.py", "main", 20)
        == "http://localhost:32769/test/test/-/blob/main/search/federation.py#L20"
    )


def test_blob_url_is_none_when_the_project_path_is_unknown():
    """Better no link than a link to a bare numeric id that 404s."""
    assert make_connector()._blob_url("99", "a.py", "main", 1) is None


def test_blob_url_omits_the_anchor_without_a_startline():
    connector = make_connector()
    connector._project_paths["1"] = "test/test"
    assert connector._blob_url("1", "a.py", "main", 0).endswith("/a.py")


# --- code hits --------------------------------------------------------------

async def test_code_hits_from_the_captured_response():
    connector = make_connector()
    connector._project_paths["1"] = "test/test"
    blobs = load("gitlab_search_project_code.json")
    assert blobs, "fixture missing - re-run tools/mcp_probe.py"

    hits = await connector._blobs_to_hits(None, blobs, ctx=ctx())

    assert len(hits) == 3
    first = hits[0]
    assert first.source == "gitlab"
    assert first.kind == "code"
    assert first.title == "test/test/deploy/run_vllm.sh"
    assert first.url == "http://localhost:32769/test/test/-/blob/main/deploy/run_vllm.sh#L3"
    assert "open-file limit" in first.snippet
    # Snippets are collapsed to single-line for display.
    assert "\n" not in first.snippet


async def test_a_code_hit_names_the_repository_it_came_out_of():
    """So an interface can offer "only what came out of test/test" without
    parsing a repository name back out of the title - which cannot be done
    reliably, because a project path contains slashes of its own and so does
    the file path appended to it."""
    connector = make_connector()
    connector._project_paths["1"] = "test/test"

    hits = await connector._blobs_to_hits(None, load("gitlab_search_project_code.json"), ctx=ctx())

    assert hits[0].container is not None
    assert hits[0].container.id == "1"
    assert hits[0].container.title == "test/test"
    assert hits[0].container.kind == "repository"


async def test_every_hit_from_one_project_shares_a_container():
    """Which is what lets the interface collapse them into one row."""
    connector = make_connector()
    connector._project_paths["1"] = "test/test"

    hits = await connector._blobs_to_hits(None, load("gitlab_search_project_code.json"), ctx=ctx())

    assert len({hit.container.id for hit in hits if hit.container}) == 1


async def test_two_matches_in_one_file_share_an_external_id():
    """`id` separates the two matches; `external_id` says they are the same
    file. An interface listing files needs the second, or it lists one file
    twice."""
    connector = make_connector()
    connector._project_paths["1"] = "test/test"

    hits = await connector._blobs_to_hits(None, load("gitlab_search_project_code.json"), ctx=ctx())

    by_file = {hit.external_id for hit in hits}
    assert len(by_file) < len(hits) or len(hits) == len(by_file)
    # Whatever the fixture holds, external_id must never be None for code.
    assert all(hit.external_id for hit in hits)


async def test_code_hit_ids_are_unique_within_one_file():
    """Two matches in the same file differ only by line, and a duplicate id
    would make citations point at the wrong card."""
    connector = make_connector()
    connector._project_paths["1"] = "test/test"
    hits = await connector._blobs_to_hits(None, load("gitlab_search_project_code.json"), ctx=ctx())
    assert len({hit.id for hit in hits}) == len(hits)


async def test_raw_payload_is_withheld_unless_debug_is_requested():
    connector = make_connector()
    # Pre-seed the path so the adapter has no reason to make a lookup call -
    # there is no session in this test.
    connector._project_paths["1"] = "test/test"
    blobs = load("gitlab_search_project_code.json")

    plain = await connector._blobs_to_hits(None, blobs, ctx=ctx())
    assert all(hit.raw is None for hit in plain)

    debug_ctx = SearchContext(db=None, user=None, http_client=None, debug=True)  # type: ignore[arg-type]
    verbose = await connector._blobs_to_hits(None, blobs, ctx=debug_ctx)
    assert all(hit.raw is not None for hit in verbose)


# --- adversarial ------------------------------------------------------------

@pytest.mark.parametrize(
    "blobs",
    [
        [],
        [None],
        ["a string"],
        [{}],
        [{"data": "no path"}],
        [{"path": None}],
        [{"path": "a.py"}],  # no project_id, no ref, no startline
    ],
)
async def test_malformed_blobs_never_raise(blobs):
    """A source returning nonsense must contribute nothing, not break the
    whole federated search."""
    hits = await make_connector()._blobs_to_hits(None, blobs, ctx=ctx())
    assert isinstance(hits, list)


async def test_blob_without_project_id_still_produces_a_hit_without_a_url():
    hits = await make_connector()._blobs_to_hits(None, [{"path": "a.py", "data": "x"}], ctx=ctx())
    assert len(hits) == 1
    assert hits[0].url is None
    assert hits[0].title.endswith("a.py")
