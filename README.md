# LLM-Hell

An OpenAI-compatible metrics proxy that sits between opencode (run by ~20
testers on their own machines, against their own real repos) and a pair
of GLM 4.7 / GLM 4.7 Flash vLLM endpoints on RunPod. Testers point their
`opencode.json` at this service instead of RunPod directly; the agent
loop, tool-calling, and file editing all happen inside opencode, entirely
out of this service's view. What this service does:

- authenticates each tester by a personal API key,
- routes a requested `model` id to the right RunPod endpoint,
- forwards the request byte-for-byte (streaming or not) so nothing about
  opencode's expectations of the response shape gets lost in translation,
- and records technical metrics (TTFT, tokens, cost, tool-call counts,
  errors) into Postgres and Prometheus for the Grafana dashboards.

See `docker-compose.yml` for the service layout: `api` (FastAPI, the
proxy itself), `postgres`, `prometheus`, `grafana`, and an optional
`mock-vllm` service for local development without a GPU.

## Local development (no GPU required)

```bash
cp .env.example .env
docker compose --profile dev up --build
docker compose exec api alembic upgrade head
```

This starts the whole stack plus `mock-vllm`, an OpenAI/vLLM-compatible
stub (see `tools/mock_vllm.py`) that gets seeded as the backing endpoint
for both `glm-4.7*` model ids when `USE_MOCK_VLLM=true` in `.env`. It
emulates streaming, reasoning output (both `reasoning_content` and inline
`<think>` tag styles), tool calls, and a usage-bearing final SSE chunk, so
the proxy's passthrough and metrics recording can be exercised end to end
without a real endpoint.

- API: http://localhost:8000 (`GET /api/health`, `GET /v1/models`, `POST
  /v1/chat/completions`, `GET /metrics`)
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

There's no web admin panel - create the first tester and hand them a key
via the management CLI:

```bash
docker compose exec api python manage.py create-user alice
docker compose exec api python manage.py issue-key alice --name laptop
```

`issue-key` prints the raw key exactly once; put it straight into the
tester's `opencode.json`. See `manage.py --help` (and each subcommand's
`--help`) for the rest: `revoke-key`, `list-keys`, `add-endpoint`,
`update-endpoint`, `list-endpoints`, `probe-endpoint`.

## Tester setup (opencode)

Add a custom provider to `opencode.json` (project-local or
`~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "llmhell": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "LLM-Hell",
      "options": {
        "baseURL": "http://<host>:8000/v1",
        "apiKey": "{env:LLMHELL_API_KEY}"
      },
      "models": {
        "deepseek-v4-flash": {
          "name": "DeepSeek V4 Flash",
          "reasoning": true,
          "interleaved": { "field": "reasoning" },
          "limit": { "context": 343296, "output": 32768 }
        }
      }
    }
  },
  "model": "llmhell/deepseek-v4-flash",
  "compaction": { "auto": true, "prune": true, "reserved": 8192 }
}
```

`reasoning` and `interleaved` are what keep the model's thinking out of the
normal transcript. `--reasoning-parser` makes vLLM split it into its own
field, but opencode does not guess which one - without `interleaved`
pointing at it, the thinking is rendered as ordinary assistant text,
interleaved with tool calls.

The field name is **`reasoning`**, confirmed against a real vLLM 0.26.0
response (the assistant message carries `"reasoning": null` next to
`"content"`). Older vLLM used `reasoning_content`, and opencode's enum
still lists it, but pointing at a field this server never sends has exactly
the same effect as not setting `interleaved` at all. Check yours before
trusting either name:

```bash
curl -s http://<pod>:8000/v1/chat/completions -H 'Content-Type: application/json'   -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"hi"}]}'   | python3 -m json.tool | grep -i reason
```

Use the object form, `{ "field": "reasoning" }`. opencode's published
schema also accepts a bare string there, but shipped builds reject it with
`Expected true | object | undefined` - the object works on both.

`limit` is not optional: opencode's schema requires `context` and `output`,
and without them there is no context-fill indicator and nothing for
auto-compaction to trigger against. `context` must equal the server's
`--max-model-len` (343296 here), and `output` is carved **out of** that
window rather than added to it - see the section below.

Don't hand-maintain those numbers; `manage.py opencode-config` prints this
whole block with the limits filled in from the registered endpoints.

`GET /v1/models` reports exactly which model ids are currently valid for
a given key - it's driven by whatever endpoints `add-endpoint` has
registered, not a fixed list, so check it if a model id 404s.

### Context usage and auto-compaction

opencode's context-fill indicator and its auto-compaction both come from
`limit.context` **in opencode's own config** - it never asks the server how
big the window is. So the number has to be written down client-side, and it
has to match the `--max-model-len` vLLM was started with. A mismatch is
silent in both directions: too low and opencode compacts long before it
needs to, too high and it overruns the server's window mid-session.

Rather than copying numbers by hand, generate the block from the endpoints
that are actually registered:

```bash
docker compose exec api python manage.py opencode-config --base-url http://<host>:8000/v1
```

That emits one entry per registered endpoint, with `limit.context` taken
from its `ctx_window`, plus:

```json
"compaction": { "auto": true, "prune": true, "reserved": 8192 }
```

`auto` is opencode's default already - it is stated explicitly so the
behaviour is visibly intended. `prune` drops old tool outputs first, which
in an agentic session are the bulk of the context and the least useful to
keep verbatim. `reserved` is the headroom opencode keeps free to do the
compaction itself.

Write the result to `~/.config/opencode/opencode.json` and **both the CLI
and the desktop app pick it up** - they read the same file. Use
`./opencode.json` instead to scope it to one project.

Keep `ctx_window` in step with the server whenever `--max-model-len`
changes:

```bash
docker compose exec api python manage.py update-endpoint <endpoint_id> --ctx-window 343296
```

### Reasoning levels

Pick the level with **opencode's own effort selector** (Default / Low /
Medium / High / Max), next to the model name in the composer. It sends
`reasoning_effort` on the request and the proxy forwards it untouched.

There is deliberately no `-low`/`-medium`/`-high` model id. This service
used to publish one per level, because opencode was thought to have no
notion of effort - it does, and the two mechanisms fought: the proxy
overwrote whatever the selector had chosen with the level implied by the
model id, so the selector appeared to do nothing.

The level is recorded on each request row from what the client actually
sent (`default` when nothing was), so the Grafana dashboards still break
cost and latency down by level.

One escape hatch survives, for a model that misbehaves when asked for an
effort level at all - GLM-4.7 emitted looping garbage for every value.
Clearing that endpoint's `levels` makes the proxy strip `reasoning_effort`
before forwarding, so the selector becomes a no-op for it instead of
breaking generation:

```bash
docker compose exec api python manage.py update-endpoint <endpoint_id> --disable-reasoning-levels
```

## Production (RunPod-backed)

```bash
cp .env.example .env
# set USE_MOCK_VLLM=false
docker compose up --build -d
docker compose exec api alembic upgrade head
```

Then register the two real RunPod vLLM endpoints and probe each one
before relying on it - the real reasoning delivery (`reasoning_content`
field vs. inline `<think>` tags) and native tool-call support are
endpoint-specific and unknown until checked:

```bash
docker compose exec api python manage.py add-endpoint \
  --name "GLM 4.7 planner" --base-url https://<runpod-host>/v1 --model-id glm-4.7 --role planner
docker compose exec api python manage.py probe-endpoint <endpoint-id>
```

`probe-endpoint` writes what it found to `model_endpoints.reasoning_profile`
so admins can adjust `tools_mode` there if the probe's reported handling
doesn't match; the proxy already forwards streaming and non-streaming
tool-calling requests as opencode sends them either way.

If RunPod isn't reachable directly (no public port, or you'd rather not
expose vLLM to the internet) and you're tunneling instead, bind the
tunnel on `0.0.0.0`, not the default loopback-only - a port only bound to
127.0.0.1 on this host is invisible from inside the `api` container on
native Linux Docker:

```bash
ssh -L 0.0.0.0:8000:127.0.0.1:8000 -L 0.0.0.0:8002:127.0.0.1:8002 <runpod-ssh-target>
```

then use `host.docker.internal` (routed in via `extra_hosts` in
`docker-compose.yml`) instead of `<runpod-host>` as the endpoint's host:

```bash
docker compose exec api python manage.py add-endpoint \
  --name "GLM 4.7 planner" --base-url http://host.docker.internal:8000/v1 --model-id glm-4.7 --role planner
docker compose exec api python manage.py add-endpoint \
  --name "GLM 4.7 Flash executor" --base-url http://host.docker.internal:8002/v1 --model-id glm-4.7-flash --role executor
```

## Backend development

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate  # .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
pytest
```

Database migrations:

```bash
alembic upgrade head
```

## Project layout

```
backend/
  manage.py       operator CLI: users, API keys, model endpoints, probing
  app/
    api/          FastAPI routers: openai_proxy (/v1/*), metrics (/metrics)
    core/         config, db, security (argon2), api_keys (bearer auth)
    models/       SQLAlchemy: User, ApiKey, ModelEndpoint, LlmRequest
    services/
      llm/        reasoning profiles, tokenizer, model routing, endpoint probe
      stats/      recorder (llm_requests + Prometheus), prom (metric defs)
    alembic/ tests/
tools/mock_vllm.py  local stand-in for the RunPod vLLM endpoints
grafana/ prometheus/  dashboards and scrape config
```

All code, comments, and developer docs are in English.
