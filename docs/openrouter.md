# Serving both models through OpenRouter

The app calls any endpoint that speaks the OpenAI chat API, so a hosted
gateway and a self-hosted vLLM server are the same thing to it — only the URL,
the key and the model id differ. Nothing in the code special-cases OpenRouter.

Two jobs are done, and they are separate concerns even when one model does
both:

| role | what it does | why it is separate |
| --- | --- | --- |
| **answer** | reads the fused search results and writes the cited answer | reasons; sees the question |
| **vision** | turns a PDF page image into text | transcribes; never sees the question |

They may be one endpoint or two. `ANSWER_MODEL_ID` and `VISION_MODEL_ID` are
matched against the `model_id` of any **enabled** endpoint, so pointing both
at the same multimodal model needs one row and no special case.

## One model for both

The simplest setup, if the model accepts images:

```bash
docker compose exec api python manage.py add-endpoint   --name "Gemma 4 31B (OpenRouter)"   --base-url https://openrouter.ai/api/v1   --model-id google/gemma-4-31b-it   --api-key sk-or-v1-... --role executor --ctx-window 262144
```

```
ANSWER_MODEL_ID=google/gemma-4-31b-it
VISION_MODEL_ID=google/gemma-4-31b-it
```

Measured against the two-model setup on the same datasheet and the same
questions:

| | Gemma 4 31B, both roles | DeepSeek + Qwen3-VL |
| --- | --- | --- |
| probe image | 4/4 words, **39s** | 4/4 words, **1s** |
| 4-page datasheet | ~150s | ~30s |
| text2sql | 4.5s, `mode=llm` | 2.4s, `mode=llm` |
| answer on a diagram | conservative — states only what the transcription supports | inferred pin pairings the transcription did not contain |

One model is simpler to run and to reason about, and its answers stuck closer
to the evidence. It is markedly slower at images — which matters less than it
looks, because transcription happens once per document, in the background,
and is then cached forever.

A `:free` variant of the same model exists and is rate-limited; use it to try
the setup, not to demonstrate it.

---

## Setting it up

```bash
docker compose exec api python manage.py add-endpoint \
  --name "DeepSeek V4 Flash (OpenRouter)" \
  --base-url https://openrouter.ai/api/v1 \
  --model-id deepseek/deepseek-v4-flash-0731 \
  --api-key sk-or-v1-... --role executor --ctx-window 131072
```

```bash
docker compose exec api python manage.py add-endpoint \
  --name "Qwen3-VL 8B Instruct (OpenRouter)" \
  --base-url https://openrouter.ai/api/v1 \
  --model-id qwen/qwen3-vl-8b-instruct \
  --api-key sk-or-v1-... --role vision --ctx-window 32768
```

Then in `.env`:

```
ANSWER_MODEL_ID=deepseek/deepseek-v4-flash-0731
VISION_MODEL_ID=qwen/qwen3-vl-8b-instruct
VISION_CONCURRENCY=4
```

and restart the API. The two ids must match the `model_id` of an **enabled**
endpoint exactly; a mismatch is logged and the answer falls back to the first
enabled endpoint, while vision simply switches off.

---

## Choosing the model ids

`deepseek/deepseek-v4-flash-0731` is the dated checkpoint — the same weights
`run_serving.sh` downloads from Hugging Face, so answers stay comparable with
anything a local pod produced. That is why it is pinned here rather than
`deepseek/deepseek-v4-flash`, which tracks the latest revision and is about
40% cheaper but can change under you.

**Take `qwen/qwen3-vl-8b-instruct`, not `qwen/qwen3-vl-8b-thinking`.** Both
exist and only one is usable here. The thinking variant spends its whole token
budget on reasoning and returns `finish_reason: length` with an **empty**
`content` — the reasoning lands in a separate field the transcriber does not
read, so every page comes back blank and the document is indexed with nothing.
Measured locally: the thinking model produced 3031 characters of deliberation
and 0 characters of output, at 124s a page; the Instruct model read the same
page in 22s.

Check that a candidate accepts images before wiring it in — OpenRouter
publishes this and no key is needed:

```bash
curl -s https://openrouter.ai/api/v1/models | grep -o '"id":"qwen/qwen3-vl[^"]*"'
```

An `input_modalities` without `image` cannot do this job at all.

---

## What it costs

A page is one request: roughly 1200 prompt tokens for the image plus a few
hundred of output. At `qwen/qwen3-vl-8b-instruct` prices ($0.117 / $0.455 per
million) that is about **$0.0003 a page**, so a four-page datasheet costs a third of a cent — **once**. The
transcription is stored in `document_pages` and never recomputed, so the
second search over the same document is free.

Two bounds keep it that way, and both matter more now that pages are billed:

- `VISION_MAX_PAGES` caps how many pages of one document are ever read. Pages
  are chosen by how much of them is picture rather than text, so the cap
  spends the budget on diagrams and skips the prose.
- Reading happens **once per document version**, in the background, and blank
  pages are recorded as blank so they are not retried.

---

## Without a key

Worth knowing what it looks like, because it is not a crash. The search still
runs and still returns hits — the Postgres source falls back to its
deterministic query rather than asking a model for SQL — and the answer reads:

```
(no answer: deepseek/deepseek-v4-flash-0731 returned 401: ...)
```

So a missing or expired key costs the answer and the illustrations, never the
result list. Add it to both endpoints with:

```bash
docker compose exec api python manage.py update-endpoint <endpoint_id> --api-key sk-or-v1-...
```

---

## Things that differ from a local server

**Turning off reasoning takes a different field.** This matters more than it
sounds: text2sql is a mechanical translation, and a reasoning model that
deliberates over it spends its whole budget and returns an empty `content`.

`chat_template_kwargs` is the vLLM lever. OpenRouter **accepts it and does
nothing with it** — measured against `deepseek-v4-flash-0731`: 1024 reasoning
tokens, empty output, 8.4s, and a request that looked configured but was not.
Its own field is `reasoning`, and only one form of it works:

| sent | result |
| --- | --- |
| `{"reasoning": {"enabled": false}}` | **1.6s, valid SQL, 0 reasoning tokens** |
| `{"reasoning": {"max_tokens": 0}}` | 4.8s, still reasoned 563 tokens |
| `{"reasoning": {"exclude": true}}` | 18.2s, empty output — hides the reasoning, does not stop it |

Both dialects are sent together; each is inert where it does not apply. A 400
on a request carrying them is retried once without them, so a gateway that
rejects the fields outright costs the optimisation and not the call.

**Concurrency.** `VISION_CONCURRENCY=4` is right for a gateway. It is wrong
for a single-GPU local server: Ollama answered one of four parallel requests
and failed the other three as transport errors, and because a failed page is
treated as a missing illustration rather than an error, the document was
quietly indexed with one page of four. If you ever point `VISION_MODEL_ID` at
something local, set this to 1.

**Keys never leave the server.** Endpoints have no API schema and are managed
only through `manage.py`, so an upstream key is not exposed by any route.
It is stored in the database in plaintext, which is the same trust boundary as
`.env` — treat a database dump accordingly.
