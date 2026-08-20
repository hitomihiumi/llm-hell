# The OpenAI-compatible proxy

This predates the knowledge base and is unchanged by it. `/v1/*` is an
OpenAI-compatible endpoint that opencode (running the agent loop on a
tester's own machine) talks to instead of a RunPod vLLM endpoint directly.
Every call is authenticated by a personal API key, routed to the right
endpoint, forwarded byte-for-byte, and metered into Postgres and Prometheus.

It shares only the `users` table with the web app. A session cookie does not
authenticate the proxy, and an API key does not authenticate the web app —
deliberately, so the two cannot break each other.

Streaming is a **byte-level passthrough**, not a re-serialisation: this
service does not understand every field a given vLLM build might attach to a
chunk, so re-encoding JSON it parsed itself would silently drop whatever it
did not know to look for. The raw SSE bytes go straight through, while a copy
is parsed on the side purely to produce metrics.

## Issuing a key

```bash
docker compose exec api python manage.py create-user alice
docker compose exec api python manage.py issue-key alice --name laptop
```

`issue-key` prints the raw key exactly once. See `manage.py --help` for
`revoke-key`, `list-keys`, `add-endpoint`, `update-endpoint`,
`list-endpoints`, `probe-endpoint`.

## Tester setup (opencode)

Add a custom provider to `opencode.json` (project-local, or
`~/.config/opencode/opencode.json` — the CLI and the desktop app read the
same file):

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

Don't hand-maintain the numbers — `manage.py opencode-config` prints this
block with the limits filled in from the registered endpoints:

```bash
docker compose exec api python manage.py opencode-config --base-url http://<host>:8000/v1
```

### Why each field is there

**`reasoning` + `interleaved`** keep the model's thinking out of the normal
transcript. `--reasoning-parser` makes vLLM split it into its own field, but
opencode does not guess which one — without `interleaved` pointing at it, the
thinking renders as ordinary assistant text, interleaved with tool calls.

The field name is **`reasoning`**, confirmed against a real vLLM 0.26.0
response. Older vLLM used `reasoning_content`, and opencode's enum still
lists it, but pointing at a field the server never sends has exactly the same
effect as not setting `interleaved` at all. Check yours:

```bash
curl -s http://<pod>:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"hi"}]}' \
  | python3 -m json.tool | grep -i reason
```

Use the object form, `{ "field": "reasoning" }`. The published schema also
accepts a bare string, but shipped builds reject it with
`Expected true | object | undefined`.

**`limit`** is not optional. opencode's context-fill indicator and its
auto-compaction both come from `limit.context` **in opencode's own config** —
it never asks the server how big the window is. So the number has to be
written down client-side and has to match the `--max-model-len` vLLM was
started with. A mismatch is silent in both directions: too low and opencode
compacts long before it needs to, too high and it overruns the window
mid-session. `output` is carved **out of** that window, not added to it.

Keep `ctx_window` in step whenever the server's `--max-model-len` changes:

```bash
docker compose exec api python manage.py update-endpoint <endpoint_id> --ctx-window 343296
```

**`compaction`** — `auto` is opencode's default already and is stated
explicitly so the behaviour is visibly intended. `prune` drops old tool
outputs first, which in an agentic session are the bulk of the context and
the least useful to keep verbatim. `reserved` is the headroom opencode keeps
free to perform the compaction itself.

`GET /v1/models` reports exactly which model ids are valid for a given key —
it is driven by the registered endpoints, not a fixed list, so check it if a
model id 404s.

## Reasoning levels

Pick the level with **opencode's own effort selector** (Default / Low /
Medium / High / Max), next to the model name in the composer. It sends
`reasoning_effort` and the proxy forwards it untouched.

There is deliberately no `-low`/`-medium`/`-high` model id. This service used
to publish one per level, because opencode was thought to have no notion of
effort — it does, and the two mechanisms fought: the proxy overwrote whatever
the selector had chosen with the level implied by the model id, so the
selector appeared to do nothing.

The level is recorded on each request row from what the client actually sent
(`default` when nothing was), so the Grafana dashboards still break cost and
latency down by level.

One escape hatch survives, for a model that misbehaves when asked for an
effort level at all — GLM-4.7 emitted looping garbage for every value.
Clearing that endpoint's `levels` makes the proxy strip `reasoning_effort`
before forwarding, so the selector becomes a no-op for it instead of breaking
generation:

```bash
docker compose exec api python manage.py update-endpoint <endpoint_id> --disable-reasoning-levels
```

## Registering real endpoints

```bash
docker compose exec api python manage.py add-endpoint \
  --name "DeepSeek V4 Flash" --base-url https://<runpod-host>/v1 \
  --model-id deepseek-v4-flash --role planner
docker compose exec api python manage.py probe-endpoint <endpoint-id>
```

`probe-endpoint` writes what it found to `model_endpoints.reasoning_profile`,
so an admin can correct `tools_mode` there if the probe's reported handling
does not match.

If RunPod is not reachable directly and you are tunnelling, bind the tunnel
on `0.0.0.0` rather than the default loopback — a port bound only to
127.0.0.1 on the host is invisible from inside the `api` container on native
Linux Docker:

```bash
ssh -L 0.0.0.0:8000:127.0.0.1:8000 <runpod-ssh-target>
```

then use `host.docker.internal` (routed in via `extra_hosts` in
`docker-compose.yml`) as the endpoint host.

## Serving the model

`tools/` holds the RunPod-side scripts. `run_deepseek.sh` is standalone: it
raises the open-file limit, clears the GPUs, installs the CUDA toolkit, makes
a uv virtualenv, downloads the checkpoint and launches vLLM with
data-parallel 4 and expert parallelism.

Two failures worth knowing before they cost you an afternoon:

- **The open-file limit.** Data-parallel opens a coordinator, one API server
  and one engine core per rank, plus several ZMQ sockets each. At the default
  1024 descriptors the coordinator dies before it can report anything, and
  the only symptom is `DP Coordinator process failed to report ZMQ addresses
  within timeout=120 seconds`.
- **KV cache must be fp8.** DeepSeek V4's sparse-MLA attention implements no
  other cache layout and asserts during `load_model()`, before any cache is
  allocated — so there is nothing to fall back to.
