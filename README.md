# LLM-Hell — federated knowledge base

Search Google Workspace, GitLab and a Postgres knowledge base from one box,
and get an answer written over the results with citations that link back to
the exact document, file or row they came from.

Each source is reached through its own **MCP server**; the service is the MCP
*client*. The LLM endpoint that used to be merely proxied is now the answer
engine — it reads the search results and writes the answer.

```
Next.js (:3001) ──/api/*──> FastAPI (:8000)
                                 │
                   ┌─────────────┼──────────────┬──────────────┐
                   │             │              │              │
             gitlab-mcp    postgres-mcp    google-mcp     ModelEndpoint
             (http /mcp)   (sse /sse)      (http /mcp)    (vLLM: answers)
```

A search fans out to every enabled source in parallel, normalises the results
into one shape, fuses the rankings, and streams the answer back over SSE.
**One unreachable source degrades the result list; it never empties it.**

## What it does

- **Two layouts** over the same pipeline, switchable in the header.
  **Search** keeps the machinery visible: per-source timings, the SQL that was
  generated, the whole ranked list at once. **Chat** is the familiar
  conversational shape, with sources behind a per-answer disclosure and
  follow-up questions carrying the previous turns.
- **Minimal auth** — username + password, httpOnly cookie session.
- **Federated search** with a deep link on every hit: a Drive/Gmail
  permalink, a GitLab blob URL with a line anchor, or an internal record page
  for a database row.
- **A cited answer, rendered as Markdown.** Models answer in Markdown, so
  headings, lists, tables and code blocks are set in the same type system the
  Borzo site uses for its writing. Every `[n]` is resolved against the hits
  that were actually in the prompt — including inside a list item or a table
  cell — and a number the model invented is dropped and counted, never
  rendered as a link.
- **Read the source without leaving.** A card shows a 400-character excerpt
  centred on the match, which answers "is this relevant" and not "what does
  it say". **View** opens the whole thing — a GitLab README rendered as
  Markdown, a source file in monospace, a database row as its columns.
- **Per-source honesty.** Each source reports its hit count, latency, and —
  when it failed — why. The Postgres source also shows the SQL it generated
  and whether that came from the model or the deterministic fallback.

The original OpenAI-compatible metrics proxy is untouched and still serves
`/v1/*` for opencode; see [docs/proxy.md](docs/proxy.md).

---

## Quick start

```bash
cp .env.example .env
# edit .env: at minimum set POSTGRES_PASSWORD and USE_MOCK_VLLM=true
docker compose --profile dev up -d --build
```

`--profile dev` adds `mock-vllm`, a GPU-free stand-in for the answer model,
so the whole pipeline works without a RunPod pod.

Create an account:

```bash
docker compose exec api python manage.py create-user demo --password 'choose-something' --role admin
```

Then open **http://localhost:3001** and sign in.

| service | URL | notes |
| --- | --- | --- |
| web | http://localhost:3001 | the demo app |
| api | http://localhost:8000 | `/api/*` and the `/v1/*` proxy |
| grafana | http://localhost:3000 | unchanged; owns 3000, which is why web is on 3001 |
| prometheus | http://localhost:9090 | |

Migrations run automatically when `api` starts.

---

## Connecting the sources

Nothing needs to be connected for the app to start. An unconfigured source
reports why on the **Sources** page rather than disappearing.

### Postgres knowledge base — works out of the box

`docker/postgres/initdb/` creates a separate `kb` database, a read-only
`kb_ro` role, and a 50-row demo corpus across four tables. Full runbook:
**[docs/test-database.md](docs/test-database.md)** — bringing it up, adding
your own tables, and proving the read-only boundary holds.

Searching it means asking the model to write a `SELECT`. That path has four
layers of protection, and **only the last two matter**:

1. the model is given the real schema, so it does not guess;
2. a validator rejects anything that is not a single read-only statement;
3. `postgres-mcp` runs `--access-mode=restricted`, parsing every statement;
4. `kb_ro` is `SELECT`-only, with a 5s statement timeout, against a database
   that does not contain the application's own tables and which it cannot
   even `CONNECT` to.

Layer 4 is why a prompt injection hidden in an indexed document is not a
vulnerability — the SQL it induces simply fails. Verify it yourself:

```bash
docker compose exec -e PGPASSWORD=kb_ro_password postgres \
  psql -U kb_ro -d kb -c "SELECT * FROM users"
# ERROR: relation "users" does not exist
```

> The initdb scripts only run on an **empty** volume. On an existing stack
> they are skipped silently, and the source reports that the tables do not
> exist. Apply them by hand — see [docs/test-database.md](docs/test-database.md).

### GitLab

Create a **classic** personal access token with the `read_api` scope, then:

```bash
GITLAB_PERSONAL_ACCESS_TOKEN=glpat-...
GITLAB_API_URL=http://host.docker.internal:32769/api/v4   # your instance
GITLAB_WEB_URL=http://localhost:32769                     # what a browser can reach
GITLAB_MCP_AUTH_TOKEN=<any random string>
```

Three things that will otherwise cost you an hour each:

- **A fine-grained token needs `Metadata: Read` + `Projects: Read` +
  `Code: Read`.** Short of that every call returns
  `403 insufficient_granular_scope`. A classic `read_api` token is one
  checkbox.
- **`search_code` does not work on Community Edition.** Instance-wide code
  search is GitLab *advanced search*, which is Elasticsearch-backed and
  Premium/Ultimate only; CE answers `400 scope does not have a valid value`.
  The connector detects this once and switches to per-project search, which
  works on any tier. Set `GITLAB_DEFAULT_PROJECT_IDS` to skip the discovery
  round-trip.
- **`GITLAB_MCP_AUTH_TOKEN` is not a GitLab credential.** It is a shared
  secret gating the sidecar's own HTTP endpoint, which the server insists on
  before it will serve a server-side PAT over the network.

### Google Workspace

The awkward one: the MCP server is stdio-only, answers in Markdown rather
than JSON, and needs a browser consent that cannot happen in a container. It
is behind an opt-in profile for that reason.

```bash
docker compose --profile google up -d --build google-mcp
```

Follow **[docs/google-workspace-setup.md](docs/google-workspace-setup.md)** —
it is a step-by-step runbook, and the OAuth flow is done once on a
workstation with the resulting tokens mounted in.

---

## Operating it

```bash
docker compose exec api python manage.py list-sources
docker compose exec api python manage.py set-password demo 'new-password'
docker compose exec api python manage.py list-endpoints
```

Pin which model writes answers with `ANSWER_MODEL_ID=<model_id>`; leaving it
empty uses the first enabled endpoint. If no endpoint is registered at all,
search still returns its hits and simply has no answer — the result list
never depends on the LLM being up.

### Checking a source

What a source can actually do is not knowable from configuration. Press
**Check** on the Sources page, or:

```bash
curl -X POST localhost:8000/api/sources/gitlab/check -b cookies -H "X-CSRF-Token: ..."
```

It asks the server for its tool list and stores the answer, which is how you
find out that a GitLab instance has no code search or that the `search`
toolset was never enabled.

### Probing a server directly

```bash
python tools/mcp_probe.py --url http://localhost:3102/mcp \
  --header "Authorization: Bearer $GITLAB_MCP_AUTH_TOKEN" list

python tools/mcp_probe.py --sse http://localhost:8002/sse \
  call execute_sql '{"sql":"SELECT 1"}'
```

Standalone — no app, no database. **None of these servers documents its
response shapes**, so this is how the result adapters were written; see
[docs/mcp-spike-findings.md](docs/mcp-spike-findings.md) for what probing
actually found, most of which contradicted the documentation.

---

## Development

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # .venv/bin/activate elsewhere
pip install -r requirements-dev.txt
pytest
ruff check .
```

```bash
cd frontend
pnpm install
pnpm dev          # :3000 by default; use --port 3001 to match compose
pnpm lint
```

**Do not `pnpm build` while `pnpm dev` is running.** Both write `.next`, and
the production build leaves the dev server's SSR workers crashing with
`Jest worker encountered 2 child process exceptions` — an error that names
nothing useful and looks like a bug in whichever page you open next. Use
`pnpm exec tsc --noEmit` to typecheck against a live dev server, and if the
two have already collided, `rm -rf .next` and restart `pnpm dev`.

`.claude/launch.json` has a `web-verify` entry that serves the production
build on :3002, which is how a page can be checked without touching the dev
server at all.

Tests use in-memory SQLite, so **no Postgres-only column types** may appear
in `app/models/` — no `JSONB`, `ARRAY`, `UUID`, `TSVECTOR`. The first one
added takes the whole API-level suite down with it.

Migrations are not exercised by the suite (`conftest.py` builds the schema
with `create_all`), so check them by hand before committing one:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
docker compose exec api alembic upgrade head
```

### Layout

```
backend/
  manage.py            operator CLI: users, keys, endpoints, sources
  app/
    api/               auth, search, sources, records, debug, openai_proxy, metrics
    core/              config, db, security, sessions (cookie), api_keys (bearer)
    models/            User, UserSession, Source, SearchQuery, ModelEndpoint, LlmRequest
    services/
      mcp/             transport (the ONLY file importing the MCP SDK),
                       connector protocol, registry, and one module per source
      search/          federation, RRF ranking, text2sql, answer synthesis
      llm/             chat client, reasoning parser, tokenizer
frontend/src/
  i18n/                locale cookie, dictionary, plural rules (en + uk)
  proxy.ts             auth gate (Next 16: this replaces middleware.ts)
  app/(app)/           search, sources, records — behind the real auth check
  app/api/search/stream/route.ts   SSE passthrough
docker/
  google-mcp/          the stdio->HTTP bridged Google server
  postgres/initdb/     kb database, kb_ro role, demo corpus
tools/mcp_probe.py     standalone MCP prober
docs/                  spike findings, Google runbook, test-database runbook, proxy notes
```

All code, comments and developer docs are in English.
