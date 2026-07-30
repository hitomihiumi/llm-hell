# LLM-Hell

An OpenAI-compatible metrics proxy that sits between opencode (run by ~20
testers on their own machines, against their own real repos) and a pair
of GLM 4.7 / GLM 4.7 Flash vLLM endpoints on RunPod. Testers point their
`opencode.json` at this service instead of RunPod directly; the agent
loop, tool-calling, and file editing all happen inside opencode, entirely
out of this service's view. What this service does:

- authenticates each tester by a personal API key,
- routes a requested `model` id (including a `-{level}` suffix picking a
  reasoning level, e.g. `glm-4.7-high`) to the right RunPod endpoint,
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
        "glm-4.7": { "name": "GLM 4.7" },
        "glm-4.7-high": { "name": "GLM 4.7 (reasoning: high)" },
        "glm-4.7-flash": { "name": "GLM 4.7 Flash" }
      }
    }
  },
  "model": "llmhell/glm-4.7"
}
```

`GET /v1/models` reports exactly which model ids are currently valid for
a given key - it's driven by whatever endpoints `add-endpoint` has
registered and their `reasoning_profile`, not a fixed list, so check it
if a model id 404s.

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
