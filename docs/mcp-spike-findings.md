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

## GitLab — NOT YET PROBED (blocked on a personal access token)

Two things to settle the moment a PAT is available:

1. **Does `search_code` exist on the target instance at all?**
   Instance-wide code search is GitLab *advanced search* — Elasticsearch-backed,
   and Premium/Ultimate on self-managed. On a Free/CE instance the tool is
   either absent or returns empty. Fallback is `search_project_code` scoped to
   configured project ids, or `search_repositories` + `list_commits`.
2. **Do results carry `web_url`?** The underlying REST objects do, but these
   servers are thin passthroughs and the README documents no response
   schema. If code hits lack it, the permalink has to be synthesised from
   `project_path` + `ref` + `path` + `startline`.

Note on the source: `harshmaur/gitlab-mcp` (the link this project started
from) is a 1-star fork last pushed 2025-06-05 whose own README redirects to
upstream, and it lacks `search_code` entirely. Use `zereight/gitlab-mcp`.

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
