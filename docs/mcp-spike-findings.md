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


## Google Workspace (`@aaronsb/google-workspace-mcp` v4.2.1) — probed

Probed against a real account with a Desktop OAuth client. **Almost
everything believed about this server before probing was wrong**, including
things stated confidently in earlier versions of this file.

### It does NOT use `gws`, Google's Workspace CLI

The widely-repeated claim — that the package shells out to that Rust binary,
which is indeed not one of its three dependencies — is false for v4.2.1.
Verified by reading the installed package:

- `build/google/client.js` calls Google's REST APIs directly with `fetch` and
  `Authorization: Bearer <token>`.
- `build/accounts/` implements its own OAuth and refreshes its own tokens
  against `oauth2.googleapis.com/token`.
- The only `gws` strings anywhere are a `gws://` URI scheme for MCP resources
  and temp-file name prefixes.
- The only child process it ever spawns is a browser, during consent.

So `@googleworkspace/cli` should not be installed at all. This repo's
Dockerfile was downloading a Rust binary for nothing.

### It answers in Markdown, not JSON

The big one. `structuredContent` is **always null**, and the text block is a
human-readable report:

```
## Files (1)

1AbCdEfG…9abc | TEST | g/document | Aug 17 | 2.2 KB

---
**Next steps:**
- Get file details: `manage_drive` — `{"operation":"get",…}`

---
**Session context** (demo@example.com):
- No new unread emails since session start (2 unread, 2 today)
```

None of that parses as JSON or as a Python literal, so the payload ladder
yields nothing — an adapter expecting JSON dicts gets zero hits from a
perfectly successful search. The connector parses the pipe-delimited table
itself and stops at the first `---`, because the "Next steps" boilerplate
contains ids, emails and pipes of its own and would otherwise be read as
results.

Gmail is the same shape with different columns:
`<messageId> | <from, truncated with …> | <subject> | <date>`.
An empty result is prose: `No messages found for query: "test".`

### Search returns metadata only — no content, and no link

A Drive row carries an id, a name, an abbreviated type, a date and a size.
No snippet, no URL.

- **Links are synthesised per type**, because a Google Doc opened at the
  generic `/file/d/<id>/view` viewer behaves badly: `g/document` →
  `docs.google.com/document/d/<id>/edit`, `g/spreadsheet` → the Sheets
  editor, and so on.
- **Content needs a second call.** Without it an answer has titles to cite
  and nothing to quote, which defeats the purpose of citations. The connector
  enriches the top `GOOGLE_ENRICH_HITS` (default 3) hits via
  `manage_docs get` / `manage_email read`.

Those two responses have **different** shapes, which is why the body
extractor cannot simply split on `---`:

```
manage_docs get    ## title, **Key:** value lines, ---, BODY, ---, boilerplate
manage_email read  ## title, **Key:** value lines, BODY, then boilerplate
```

There is also **no match highlight anywhere** in the response — nothing says
which part of the document the query hit. Taking the document's opening as
the excerpt therefore explains a page-five hit with a first-paragraph quote,
so `connector.excerpt_around` locates the passage itself and cuts a window
around it. The same helper serves Postgres, whose rows arrive as a whole
column with the same problem. GitLab needs none of this: its `data` field is
already the matched lines.

### Dates omit the year

`Aug 17`, with no year, for recent items. Assuming the current year
unconditionally would date a December item into the future and hand it an
undeserved recency boost, so the parser rolls back a year when the result
would be in the future.

### Every tool requires `email`; unknown keys are tolerated anyway

`email` is in `required` on every tool — the server is multi-account and has
no default, so there is nothing to fall back to. Both schemas also declare
`additionalProperties: false`, but the runtime **ignores** unknown keys: a
deliberately-sent `pageSize` was accepted, not rejected. Do not rely on the
server to catch a misspelled argument.

Drive's `query` is **Drive's query language**, not free text — its own
description says `name contains 'budget'`. A bare phrase is a syntax error,
so the connector wraps input as `fullText contains '…'`. Gmail's query does
accept bare terms.

### 11 tools, not 12, and no `manage_contacts`

`manage_accounts`, `manage_workspace`, `manage_scratchpad`,
`queue_operations`, `manage_calendar`, `manage_docs`, `manage_drive`,
`manage_email`, `manage_meet`, `manage_sheets`, `manage_tasks`.

### The stdio→HTTP bridge must be stateful

`supergateway` defaults to stateless and respawns the node server per
request, costing ~5s per Drive search — and since the fan-out waits for its
slowest source, that was ~5s on *every* search in the application. With
`--stateful`, a Drive search plus enrichment is ~3.5s and a plain search
~1.2s, which is Google's own API latency.

### Consent grants read/write

`authenticate` takes no scope argument and requests the server's full set:
`drive`, `gmail.modify`, calendar, sheets, docs, tasks, slides, meet. There
are no read-only variants in its scope map. `manage_accounts` with
`operation: "scopes"` and `services: "drive,gmail"` narrows it afterwards, at
the cost of a second consent.
