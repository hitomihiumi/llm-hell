# MCP spike findings

What the three MCP servers **actually** do, as opposed to what their
READMEs say. Captured with `tools/mcp_probe.py`; the raw responses are in
`backend/tests/fixtures/mcp/` and are the input to the adapter unit tests.

Re-run the probe and update this file whenever a server is upgraded. Every
item below was a guess that turned out to be wrong, and each one would have
produced a connector that silently returned nothing.

---

## postgres-mcp (`crystaldba/postgres-mcp`) — probed, working

### Transport: SSE only, not streamable-HTTP

The released image supports exactly two transports:

```
--transport {stdio,sse}
```

`--transport streamable-http` is rejected outright, and the flags are
`--sse-host` / `--sse-port`, not `--host` / `--port`. Reports of a
streamable-http option describe an unreleased build. The endpoint is
`/sse`, and the client is `mcp.client.sse.sse_client`, which yields a
**2-tuple** where `streamablehttp_client` yields a 3-tuple.

### Responses are Python `repr()`, not JSON

This is the big one. Every tool returns:

- `structuredContent`: **always `null`**. Never populated, for any tool.
- one `text` content block containing `str(python_object)` — single-quoted
  keys, `None` rather than `null`, and for `execute_sql`, embedded
  `datetime.datetime(2026, 8, 16, 7, 59, 28, tzinfo=...)` **constructor
  calls**.

So `json.loads` fails on all of it, and `ast.literal_eval` fails on
`execute_sql` results specifically, because a constructor call is not a
literal.

### The fix: make Postgres do the serialising

Wrap every generated query so the driver never has to render a non-literal:

```sql
SELECT json_agg(t)::text AS payload FROM ( <generated SELECT> ) t
```

The response repr is then `[{'payload': '[{"id": 5, ...}]'}]` — one row,
one column, a plain string literal. `ast.literal_eval` parses the outer
repr, `json.loads` parses the payload, and timestamps arrive as ISO
strings. Verified.

Two edge cases that come with it:

- **Zero rows gives `[{'payload': None}]`**, not `[]` — `json_agg` returns
  SQL NULL over an empty set. Treat `None` as "no hits".
- `get_object_details` returns a **dict**, not a list, and `list_schemas` /
  `list_objects` return lists. Do not assume a list.

### `is_error` is False even when the call was refused

A blocked statement comes back as a **successful** result whose text begins
with `Error: `:

```
is_error: False
text: "Error: Error validating query: DELETE FROM articles"
```

Checking `result.isError` alone therefore misses every restricted-mode
rejection, and an adapter would happily parse the refusal message as data.
Detect the `Error: ` prefix as well.

### Database isolation holds

`SELECT * FROM users` against the KB connection returns
`Error: relation "users" does not exist` — the app's tables are in a
different database and `kb_ro` has no CONNECT privilege on it. `list_objects`
sees only `articles` and `tickets`. This is the control that does not depend
on the model behaving or on the SQL validator being complete.

### Tool list (9, matches the README)

`list_schemas`, `list_objects`, `get_object_details`, `execute_sql`,
`explain_query`, `analyze_workload_indexes`, `analyze_query_indexes`,
`analyze_db_health`, `get_top_queries`.

**There is no search tool.** Searching this source means generating SQL and
calling `execute_sql`.

---

## GitLab (`zereight050/gitlab-mcp`) — probed against GitLab CE 19.2.2

Note on the source: `harshmaur/gitlab-mcp` (the link this project started
from) is a 1-star fork last pushed 2025-06-05 whose own README redirects to
upstream. Use `zereight/gitlab-mcp`.

### The code-search tools are OFF by default

A stock container exposes **63 tools and none of them searches code**.
`discover_tools` explains why:

```json
{"id": "search", "toolCount": 3, "active": false, "isDefault": false}
```

Setting `GITLAB_TOOLSETS=search,repositories,projects` enables them and cuts
the surface to 17 tools, dropping the merge-request and issue tools this
project would only list and discard.

### Streamable HTTP refuses to start with a server-side token

```
STREAMABLE_HTTP=true with server-side GitLab credentials requires
REMOTE_AUTHORIZATION=true, GITLAB_MCP_OAUTH=true, or STREAMABLE_HTTP_AUTH_TOKEN
```

A deliberate guard: otherwise anything that can reach the port inherits the
PAT. We set `STREAMABLE_HTTP_AUTH_TOKEN` — a shared secret between the api
container and the sidecar, unrelated to the GitLab credential.

### DNS-rebinding protection rejects other containers as 403

The single most misleading error in this whole integration. From the host,
`http://localhost:3102/mcp` works. From the `api` container,
`http://gitlab-mcp:3002/mcp` returns:

```
403 {"error":"Host header is not allowed",
     "hint":"Set MCP_SERVER_URL or MCP_ALLOWED_HOSTS for non-loopback /mcp hosts."}
```

The server only trusts a loopback `Host` header by default. A 403 next to a
configured bearer token reads unmistakably as an auth failure and is nothing
of the kind - the token was correct the whole time. Fixed by setting
`MCP_ALLOWED_HOSTS` to include the compose service name.

### `search_code` does not work on Community Edition

Instance-wide code search is GitLab advanced search: Elasticsearch-backed,
Premium/Ultimate only. On CE it fails with

```
GitLab API error: 400 {"error":"scope does not have a valid value"}
```

Confirmed with two different tokens, including one with every scope except
GitLab Duo — so this is an edition limitation, not a permissions problem.
**`search_project_code` works fine on CE**, so the connector searches
project by project and treats instance-wide search as an optimisation to try
once and then remember has failed.

### Code hits carry no `web_url`

A `search_project_code` result is:

```json
{"basename": "search/federation", "data": "...matched lines...",
 "path": "search/federation.py", "filename": "search/federation.py",
 "id": null, "ref": "main", "startline": 20, "project_id": "1"}
```

There is no link, so the permalink is synthesised as
`{web}/{path_with_namespace}/-/blob/{ref}/{path}#L{startline}` — which needs
a project-id-to-path map that only the project tools provide.

### `web_url` points at the instance's own hostname

Where a URL *is* returned, GitLab builds it from its configured
`external_url`. For a containerised instance that is the container hostname
(`http://aea717aec055/test/test`) — correct for the server, dead in a
browser. Every URL is rewritten onto `GITLAB_WEB_URL`.

### Shapes differ between tools, and ids change type

`search_project_code` returns a **bare JSON array**; `search_repositories`
returns **`{"count": n, "total_pages": n, "items": [...]}`**. `get_project`
returns `id` as an **int**, `search_repositories` as a **string**. Unlike
postgres-mcp, this server returns real JSON rather than a Python repr.

### The SDK raises its own error class

`session.call_tool` raises `mcp.shared.exceptions.McpError` - a *different*
class from the one defined in `transport.py`. Letting it escape means
callers catching the local error miss it, and it then crosses an anyio task
group and arrives as `unhandled errors in a TaskGroup (1 sub-exception)`
with no indication of the cause. `transport.call_tool` now wraps anything
the SDK raises, and `summarise_exception` flattens ExceptionGroups so the
per-source error shown in the UI names the real failure.

---

## Google Workspace — NOT YET PROBED (blocked on OAuth credentials)

Known from the source before probing:

- **stdio only.** `StdioServerTransport`, no HTTP/SSE option, no port. To
  run it as a sidecar it has to be bridged to HTTP; otherwise it must be a
  child process of the API.
- **Needs the `gws` binary.** The npm package declares three dependencies
  and none of them is a Google API client — it shells out to Google's
  Workspace CLI, a Rust binary that must be on `$PATH`. Unverified whether
  a Linux release is downloadable.
- **First use requires an interactive browser flow**
  (`manage_accounts {operation: "authenticate"}`), so credentials have to be
  produced on a workstation and mounted in.
- Tools are fat operation-dispatchers: search is
  `manage_drive {operation: "search"}` and `manage_email {operation: "search"}`,
  not dedicated tools.

To settle when credentials exist: the response shape of both searches, and
whether Drive hits carry `webViewLink`.
