# Serving both models through OpenRouter

The app calls any endpoint that speaks the OpenAI chat API, so a hosted
gateway and a self-hosted vLLM server are the same thing to it — only the URL,
the key and the model id differ. Nothing in the code special-cases OpenRouter.

Two endpoints are needed, and they do different jobs:

| role | what it does | why it is separate |
| --- | --- | --- |
| **answer** | reads the fused search results and writes the cited answer | reasons; sees the question |
| **vision** | turns a PDF page image into text | transcribes; never sees the question |

---

## Setting it up

```bash
docker compose exec api python manage.py add-endpoint \
  --name "DeepSeek V4 Flash (OpenRouter)" \
  --base-url https://openrouter.ai/api/v1 \
  --model-id deepseek/deepseek-v4-flash \
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
ANSWER_MODEL_ID=deepseek/deepseek-v4-flash
VISION_MODEL_ID=qwen/qwen3-vl-8b-instruct
VISION_CONCURRENCY=4
```

and restart the API. The two ids must match the `model_id` of an **enabled**
endpoint exactly; a mismatch is logged and the answer falls back to the first
enabled endpoint, while vision simply switches off.

---

## Choosing the model ids

`deepseek/deepseek-v4-flash-0731` is the same checkpoint as the self-hosted
one. `deepseek/deepseek-v4-flash` tracks the latest revision and is cheaper.
Either works; pin the dated one if you need answers to stay comparable with
what a local pod produced.

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
hundred of output. At `qwen3-vl-8b-instruct` prices that is about **$0.0003 a
page**, so a four-page datasheet costs a third of a cent — **once**. The
transcription is stored in `document_pages` and never recomputed, so the
second search over the same document is free.

Two bounds keep it that way, and both matter more now that pages are billed:

- `VISION_MAX_PAGES` caps how many pages of one document are ever read. Pages
  are chosen by how much of them is picture rather than text, so the cap
  spends the budget on diagrams and skips the prose.
- Reading happens **once per document version**, in the background, and blank
  pages are recorded as blank so they are not retried.

---

## Things that differ from a local server

**`chat_template_kwargs`.** This is how a vLLM server is told not to think on
a given request, and it is not part of the OpenAI schema. A gateway may reject
it. A 400 on a request carrying it is retried once without it — the
optimisation is lost, the call is not.

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
