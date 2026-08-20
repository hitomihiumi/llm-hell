# Connecting Google Workspace

This is the one source that cannot be set up entirely from the server. The
MCP server's OAuth consent opens a browser, so the tokens are produced
**once on your own machine** and then mounted into the container.

Budget 15–20 minutes, most of it waiting on the Google Cloud console.

---

## Why it works this way

- The MCP server (`@aaronsb/google-workspace-mcp`) speaks **stdio only** — it
  has no HTTP mode. The `google-mcp` container wraps it with `supergateway`
  so the API can reach it over HTTP like the other two sources.
- **It manages its own OAuth** and calls Google's REST APIs directly. It has
  its own account registry and its own token store, separate from anything
  else on the machine.
- The consent screen is a browser flow with no headless equivalent, so
  step 3 below happens on your workstation.

> **It does not need `gws`, Google's Workspace CLI.** A lot of writing about
> this package — including an earlier version of this document — says it
> shells out to that Rust binary. Verified against v4.2.1: it uses `fetch`
> with a bearer token against Google's APIs, the only `gws` strings in it are
> a `gws://` MCP resource URI scheme and temp-file prefixes, and the only
> child process it spawns is a browser during consent. Do not bother
> installing `@googleworkspace/cli`.

---

## 1. Create OAuth credentials

1. Open <https://console.cloud.google.com/> and select or create a project.
2. **APIs & Services → Enabled APIs & services → + Enable APIs and services**,
   and enable the APIs for whatever you want searchable. For this demo:
   - Google Drive API
   - Gmail API
3. **APIs & Services → OAuth consent screen**:
   - User type **External** is fine for a personal account; choose
     **Internal** for a Workspace organisation.
   - Fill in the app name and support email.
   - Under **Test users**, add the account you are going to search. Without
     this, consent fails with `access_blocked` while the app is unpublished.
4. **APIs & Services → Credentials → + Create credentials → OAuth client ID**:
   - Application type: **Desktop app**. This matters — a "Web application"
     client is rejected at consent because the redirect URI will not match
     the loopback address the server listens on.
   - Copy the **client ID** and **client secret**.

## 2. Put the credentials in `.env`

```bash
GOOGLE_CLIENT_ID=<your client id>
GOOGLE_CLIENT_SECRET=<your client secret>
# The account whose Drive and Gmail get searched. This is NOT optional -
# every Google tool takes `email` as a required argument, because the server
# is multi-account and has no notion of a default one.
GOOGLE_ACCOUNT_EMAIL=you@example.com
```

`.env` is gitignored. Do not put these in `.env.example`.

## 3. Authenticate on your workstation

Requires **Node.js ≥ 22.12**.

```bash
npm install -g @aaronsb/google-workspace-mcp
```

The server speaks MCP over stdin, so drive it with the inspector rather than
by hand:

```bash
GOOGLE_CLIENT_ID=<id> GOOGLE_CLIENT_SECRET=<secret> \
  npx -y @modelcontextprotocol/inspector google-workspace-mcp
```

Call `manage_accounts` with:

```json
{ "operation": "authenticate", "category": "work" }
```

A browser window opens. **Note what you are granting**: `authenticate` takes
no scope argument and requests the server's full set — read/write on Drive,
Gmail, Calendar, Sheets, Docs, Tasks, Slides and Meet. There are no
read-only variants in its scope map. If that is more than you want, complete
it and then narrow with:

```json
{ "operation": "scopes", "email": "you@example.com", "services": "drive,gmail" }
```

which triggers a second consent for the reduced set. Access can be revoked at
any time at <https://myaccount.google.com/permissions>.

The server then writes:

| what | where (Linux/macOS) |
| --- | --- |
| account registry | `~/.config/google-workspace-mcp/accounts.json` |
| refresh tokens | `~/.local/share/google-workspace-mcp/credentials/<slug>.json` |

On Windows these are under `%APPDATA%` and `%LOCALAPPDATA%`.

Verify before moving on — call `manage_drive` with:

```json
{ "operation": "search", "email": "you@example.com",
  "query": "fullText contains 'test'", "maxResults": 5 }
```

**`query` is Drive's own query language, not free text.** A bare phrase is a
syntax error, not a search. The connector builds `fullText contains '…'` for
you; this is only what to type when testing by hand. Gmail's `query` is
Gmail search syntax, where bare terms do work.

## 4. Copy the tokens into the repo

```bash
mkdir -p secrets/google-workspace/config secrets/google-workspace/share
cp -r ~/.config/google-workspace-mcp/.       secrets/google-workspace/config/
cp -r ~/.local/share/google-workspace-mcp/.  secrets/google-workspace/share/
```

`secrets/` is gitignored. These are live credentials — treat the directory
the way you would treat a private key.

## 5. Start the container

Behind a compose profile, because without credentials it starts fine and then
fails every call:

```bash
docker compose --profile google up -d --build google-mcp
docker compose logs google-mcp --tail 20
```

## 6. Enable the sources

They are seeded **disabled** when no credentials are configured, so that an
unreachable source does not add its connection timeout to every search. Turn
them on from the **Sources** page, or:

```bash
docker compose exec -T postgres psql -U llmhell -d llmhell \
  -c "UPDATE sources SET enabled = true WHERE key LIKE 'google%';"
```

Then press **Check** on the Sources page. It should report reachable with a
tool list including `manage_drive` and `manage_email`.

---

## Troubleshooting

**`access_blocked` during consent.** The OAuth app is unpublished and your
account is not in **Test users**.

**Every call fails with a schema validation error.** Both tool schemas are
`additionalProperties: false` and require `email`. An extra argument is
rejected outright rather than ignored.

**Searches work for a while and then start failing.** The token store is
mounted read-write for a reason — the server refreshes tokens and rewrites
those files. Check the mounts in `docker-compose.yml` are not `:ro`.

**Searches are slow (~5s).** The bridge must run `--stateful`, or
supergateway respawns the server on every request. Since the federated
fan-out waits for its slowest source, that penalty lands on every search in
the application, not just Google's.

**Hits have titles but no text to quote.** Search returns metadata only, so
content comes from a second call per hit, bounded by `GOOGLE_ENRICH_HITS`.
Setting it to 0 disables that and leaves answers with nothing to cite.

Which hits get that call is decided by title first, then by Drive's rank.
Asked *"according to the gantt chart, when was the team object 3d printed"*,
Drive put three weekly progress reports above the spreadsheet actually called
**Gantt Chart**, and with a budget of three the one file the question named
was never opened — it reached the answer as a title with an empty snippet.

**An answer about a spreadsheet quotes the wrong cells.** A sheet is the one
source here whose meaning is positional: a cell says `X` and nothing else, and
what it means comes from the row label to its left and the header rows above
it. Rows are therefore sent to the model addressed —

```
R4:  B=Task  C=1  D=8  E=12  F=15  G=18  H=19
R31: B=3D print parts for the Team Object  G=X  H=O
```

— so that `G` is looked up rather than arrived at by counting pipe
characters across fifty rows. The same model answered from columns four
places off when given the raw grid, and answered "September 18" when given
this one. `ANSWER_SHEET_CHARS` is the budget; when a sheet exceeds it, the
matching rows are kept first, then the header band, then the legend, because
a header band with nothing under it answers nothing. See
`backend/app/services/sheets.py`.

**Results come back but the UI shows none.** The source badge will say
*"the response could not be parsed as JSON or as a Markdown report"*. This
server answers in Markdown, not JSON, so that means its report format has
changed; capture it and fix the mapping:

```bash
python tools/mcp_probe.py --url http://localhost:3103/mcp \
  call manage_drive '{"operation":"search","email":"you@example.com","query":"fullText contains '"'"'x'"'"'","maxResults":5}' \
  --save backend/tests/fixtures/mcp/google_drive_search.json
```

**Do not run the API with multiple workers in `stdio` mode.**
`GOOGLE_MCP_MODE=stdio` makes the API spawn the server as a child process;
N workers means N processes refreshing and rewriting the same token file,
which can race and invalidate the refresh token. The default `http` mode has
no such limit, and the API logs a warning if it sees `WEB_CONCURRENCY`.
