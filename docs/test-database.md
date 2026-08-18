# The test knowledge base

The Postgres source searches a database called `kb`, which is **not** the
application's own database. It ships with a demo corpus of 50 rows across
four tables, and it is the one source that works with no external account,
no token and no OAuth consent — so it is the right thing to get running
first when you want to see the app do something.

| | |
| --- | --- |
| database | `kb` (separate from `llmhell`) |
| role the search uses | `kb_ro` — `SELECT` only, 5s statement timeout |
| tables | `articles`, `tickets`, `runbooks`, `decisions` |
| rows | 18 + 16 + 8 + 8 |
| scripts | `docker/postgres/initdb/01-create-kb.sql`, `02-seed-kb.sql` |

---

## Bringing it up

### On a fresh volume — nothing to do

`docker/postgres/initdb/` is mounted into the Postgres image's
`docker-entrypoint-initdb.d`, so both scripts run automatically the first
time the container initialises an empty data directory:

```bash
docker compose --profile dev up -d --build
```

That is the whole step. The database, the role, the tables and the corpus
all exist by the time the healthcheck passes.

### On a stack that has run before — apply by hand

**Those scripts only run against an empty volume.** If the stack has ever
started, Postgres skips the whole directory, and nothing warns you — the
source simply reports that the tables do not exist.

```bash
docker compose exec -T postgres psql -U llmhell -d postgres -v ON_ERROR_STOP=1 < docker/postgres/initdb/01-create-kb.sql
```

```bash
docker compose exec -T postgres psql -U llmhell -d kb -v ON_ERROR_STOP=1 < docker/postgres/initdb/02-seed-kb.sql
```

Substitute your own `POSTGRES_USER` for `llmhell` if you changed it.

The first script creates the database and the role, and fails on a second
run because the database already exists — that is expected, and means it
has already been applied. The second script drops and recreates its four
tables, so it is safe to run as often as you like; that is how you reset the
corpus after experimenting with it.

### Starting over completely

```bash
docker compose down -v
```

This destroys the Postgres volume — including your user accounts, sessions
and query history, not just the corpus. On the next `up` the initdb scripts
run again from scratch.

---

## What is in it

The four tables exist to give text2sql a **choice**. With a single table the
model cannot pick the wrong one, and the interesting half of the feature is
never exercised.

| table | holds | dated by | attributed to |
| --- | --- | --- | --- |
| `articles` | reference prose: how something works and why | `updated_at` | `author` |
| `tickets` | things that broke, and what they turned out to be | `created_at` | `reporter` |
| `runbooks` | ordered procedures somebody follows at 3am | `updated_at` | `owner` |
| `decisions` | the option taken, the options rejected, and why | `decided_at` | `decided_by` |

Note that the timestamp and author columns are **named differently in every
table**. That is deliberate: it is what stops the connector from getting
away with a rule keyed on the table name.

The content deliberately overlaps with the Drive document and the GitLab
project — the same topics (RRF ranking, the fp8 KV cache, the read-only SQL
role, the open-file limit) appear in more than one system. A corpus of
unrelated rows would rank perfectly well and demonstrate nothing, because
the point of the demo is one question pulling related material out of
several systems at once.

---

## Verifying it

Row counts, as `kb_ro` — the role the search actually uses, so this checks
the grants at the same time:

```bash
docker compose exec -T -e PGPASSWORD=kb_ro_password postgres psql -U kb_ro -d kb -c "SELECT 'articles' t, count(*) FROM articles UNION ALL SELECT 'tickets', count(*) FROM tickets UNION ALL SELECT 'runbooks', count(*) FROM runbooks UNION ALL SELECT 'decisions', count(*) FROM decisions ORDER BY 1"
```

Then prove the boundary holds. Generated SQL is reachable by anyone who can
get text into an indexed document, so this is worth checking after any
change to the scripts — all three of these **must fail**:

```bash
docker compose exec -T -e PGPASSWORD=kb_ro_password postgres psql -U kb_ro -d kb -c "SELECT * FROM users"
```
> `ERROR: relation "users" does not exist` — the application tables are in
> another database entirely.

```bash
docker compose exec -T -e PGPASSWORD=kb_ro_password postgres psql -U kb_ro -d llmhell -c "SELECT 1"
```
> `FATAL: permission denied for database "llmhell"` — `kb_ro` cannot even
> open a connection to it.

```bash
docker compose exec -T -e PGPASSWORD=kb_ro_password postgres psql -U kb_ro -d kb -c "DELETE FROM articles"
```
> `ERROR: cannot execute DELETE in a read-only transaction`

If any of those succeeds, the SQL guardrails are decorative. Disable the
source until it is fixed rather than relying on the prompt or the validator,
neither of which is the control.

---

## Searching it

Ask something in the web app, or hit the API directly. The **Search** layout
shows the SQL that was generated and which of two paths produced it:

- `mode: llm` — the answer model wrote the query, having been given the real
  schema. This is the path that picks a table.
- `mode: fallback` — the model was unavailable or its output failed
  validation, so a deterministic `ILIKE` query ran instead. The source never
  disappears merely because a model fumbled its JSON.

Four questions that each land on a different table:

| question | table |
| --- | --- |
| How does result ranking work? | `articles` |
| vLLM hangs at the nccl banner, what is broken? | `tickets` |
| How do I rotate a leaked GitLab token? | `runbooks` |
| Why did we choose RRF instead of normalised scores? | `decisions` |

Each hit links to `/records/{table}/{pk}`, since a database row has no
natural URL. That route resolves the table against the same whitelist rather
than trusting the path — a table that is not in `KB_SEARCH_TABLES` answers
404, identically to a row that does not exist.

> **The fallback only searches the first table.** `KB_SEARCH_TABLES[0]` is
> the one it uses, so with the model down, only `articles` is searched. Put
> your most important table first.

---

## Adding your own tables

Five steps, and skipping either of the middle two is the usual cause of
"my table is invisible":

1. **Create it**, with a text title column and a text body column. Any
   timestamp and author column names work — they are resolved against the
   introspected schema, not assumed from the table name.
2. **Grant it.** `GRANT SELECT ON ALL TABLES IN SCHEMA public TO kb_ro` only
   covers tables that existed when it ran. A new table needs the grant
   re-run, which is the safe default for a role reachable by generated SQL.
3. **Whitelist it** in `KB_SEARCH_TABLES` in `.env`. This list is both what
   the model is allowed to reference and what the validator enforces.
4. **Restart the API** so it re-reads the environment, and because the
   introspected schema is cached for the process lifetime.
5. **Check the source** on the Sources page. Its `tables` field is what the
   connector believes it can search.

To point the connector at a differently shaped corpus without renaming
anything, set `fallback_tables` in the source's `config` — an explicit
mapping always wins over the resolver.

---

## When it does not work

| symptom | cause |
| --- | --- |
| `relation "articles" does not exist` | initdb was skipped on a non-empty volume — apply `02-seed-kb.sql` by hand |
| `permission denied for table <new>` | the table was added after the `GRANT`; re-run the grant |
| a new table never appears in results | missing from `KB_SEARCH_TABLES`, or the API was not restarted |
| every search is `mode: fallback` | the answer endpoint is unreachable, or the model is not returning valid JSON — check `ANSWER_MODEL_ID` and `manage.py list-endpoints` |
| only `articles` ever comes back | that is the fallback searching `KB_SEARCH_TABLES[0]`; see above |
| the source reports 9 hits but fewer appear | working as intended — no source may take more than half the page (`per_source_cap`), so with the other sources empty the list is capped |
| `cannot execute … in a read-only transaction` | working as intended — `kb_ro` is `SELECT`-only |

The dev stack's `mock-vllm` answers text2sql prompts for real: it parses the
schema out of the prompt, picks a table from the question and emits a valid
query. That is why `mode: llm` works with no GPU. It is a keyword matcher,
not a model — treat its table choice as a smoke test, not as evidence about
how a real model will behave.
