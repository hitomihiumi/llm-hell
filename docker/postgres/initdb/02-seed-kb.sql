-- Demo corpus for the Postgres source.
--
-- Deliberately prose-heavy and overlapping with what lives in Drive and in
-- the GitLab project, because the point of the demo is that one question
-- pulls related material out of several systems at once. A corpus of
-- disjoint rows would rank fine and demonstrate nothing.
--
-- Four tables rather than one, so text2sql has to *choose* one. With a
-- single table the model cannot get the choice wrong and the interesting
-- half of that feature is never exercised.
--
-- Re-runnable. Files in /docker-entrypoint-initdb.d execute only against an
-- empty volume, so on any stack that has run before this is applied by
-- hand - see docs/test-database.md. The DROPs are what make that second
-- application work; they are scoped to the four demo tables, and this
-- database holds nothing else.

\connect kb

DROP TABLE IF EXISTS articles CASCADE;
DROP TABLE IF EXISTS tickets CASCADE;
DROP TABLE IF EXISTS runbooks CASCADE;
DROP TABLE IF EXISTS decisions CASCADE;

-- Reference prose: how something works and why.
CREATE TABLE articles (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    author      TEXT,
    category    TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Things that went wrong, and what they turned out to be.
CREATE TABLE tickets (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    reporter    TEXT,
    component   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Procedures: ordered steps somebody follows at 3am.
CREATE TABLE runbooks (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    owner       TEXT,
    system      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Decision records: the option taken, the options rejected, and why.
CREATE TABLE decisions (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    decided_by  TEXT,
    status      TEXT NOT NULL DEFAULT 'accepted',
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO articles (title, body, author, category, updated_at) VALUES
('Deploying the inference stack to RunPod',
 'The inference stack runs on 4x RTX PRO 6000 Blackwell GPUs. Start it with tools/run_deepseek.sh, which installs the CUDA toolkit, creates a uv virtualenv, downloads the checkpoint and launches vLLM with data-parallel 4 and expert parallelism. The single most common failure is the open-file limit: the data-parallel coordinator opens a socket per rank, and at the default 1024 descriptors it dies before it can report anything, leaving only a ZMQ timeout in the log. Raise it with ulimit -n 65535 in the shell that launches the server, not in a parent that has already forked.',
 'anna', 'infrastructure', now() - interval '3 days'),

('KV cache must be fp8 on DeepSeek V4',
 'DeepSeek V4 uses sparse MLA attention, which only implements an fp8 cache layout. Passing anything else makes layer construction assert during load_model, before any cache is allocated, so there is no unquantized fallback and no partially working mode. This is not a memory optimisation that can be traded away for accuracy: the kernel for the other layouts does not exist. If you see the assert, the flag is wrong, not the hardware.',
 'anna', 'infrastructure', now() - interval '11 days'),

('How search federation ranks results',
 'Results from Drive, GitLab and Postgres carry relevance scores that are not comparable, and two of the three often carry none at all. We therefore rank by reciprocal rank fusion: each hit contributes weight / (60 + rank within its own source). This ignores the source scores entirely, on purpose - treating them as commensurate would silently favour whichever backend happens to return the largest numbers. The constant 60 is the standard RRF k; it flattens the difference between rank 1 and rank 2 enough that a source with one excellent hit does not monopolise the page.',
 'dmytro', 'architecture', now() - interval '1 day'),

('The recency boost is deliberately tiny',
 'Fused scores are multiplied by a small freshness factor so that, between two otherwise equal hits, the newer one wins. The maximum boost is 0.016, which is smaller than the gap between adjacent RRF ranks within a source. That bound is the whole point: an earlier version used 0.15, which was large enough to lift a hit nine positions purely for being recent, turning a relevance ranking into a date sort. A boost that can reorder results is not a tiebreak.',
 'dmytro', 'architecture', now() - interval '6 days'),

('Onboarding: getting access to the knowledge base',
 'Ask an admin to run manage.py create-user with a password. Accounts created before the web app existed have no password hash and cannot log in until manage.py set-password is run for them; they keep working against the API-key proxy in the meantime. Roles are admin and user: only an admin can enable, disable or health-check a source, and the UI hides those controls rather than letting the request fail with a 403.',
 'kate', 'onboarding', now() - interval '20 days'),

('Why generated SQL runs as a read-only role',
 'Search over Postgres works by asking the model to write a SELECT. The prompt says SELECT only and a validator rejects anything else, but neither of those is the actual control: the sidecar connects as kb_ro, against a database that does not contain the application tables, with default_transaction_read_only and a five second statement timeout. A prompt injection in an indexed document that gets the model to emit a DELETE simply fails. If you find yourself hardening the regex because you are worried about a bypass, harden the role instead.',
 'dmytro', 'security', now() - interval '2 days'),

('Reasoning effort is chosen by the client',
 'opencode has its own effort selector and sends reasoning_effort on the request. The proxy forwards it untouched. It used to overwrite the value from a suffix on the model id, which meant the selector appeared to do nothing at all - the request said high, the server ran medium, and nothing in either log said why.',
 'anna', 'architecture', now() - interval '30 days'),

('MCP transports are not interchangeable',
 'Each of the three servers speaks a different transport, and none of them documents which. gitlab-mcp serves streamable HTTP at /mcp. postgres-mcp rejects streamable HTTP outright and serves SSE at /sse. The Google Workspace server is stdio only and has to be bridged to HTTP by supergateway before anything on the network can reach it. Assume nothing here: probe with tools/mcp_probe.py before writing an adapter.',
 'dmytro', 'architecture', now() - interval '8 days'),

('postgres-mcp never fills structuredContent',
 'Every response from that server is a Python repr inside a text block, including raw query results - which means datetime constructor calls that no safe parser will evaluate. The adapter works around it by wrapping each generated statement so Postgres itself returns JSON as text, which parses cleanly and loses no type information. Do not reach for ast.literal_eval on a repr containing constructor calls; it does not handle them and should not.',
 'dmytro', 'architecture', now() - interval '9 days'),

('The Google Workspace server answers in Markdown',
 'It does not return JSON. A Drive search comes back as a human-readable Markdown report with a table of files, and a Gmail search as a list of message summaries. The adapter parses that report rather than any structured field. This was found by probing, after an adapter written against the documented JSON shape returned zero hits from every search that had in fact succeeded.',
 'anna', 'integration', now() - interval '5 days'),

('Code search on GitLab Community Edition',
 'Instance-wide code search is GitLab advanced search, which is Elasticsearch-backed and Premium or Ultimate only. On CE the search_code tool answers 400 scope does not have a valid value. The connector detects this once and switches to per-project search, which works on every tier. Setting GITLAB_DEFAULT_PROJECT_IDS skips the project-discovery round trip and makes the first search noticeably faster.',
 'kate', 'integration', now() - interval '13 days'),

('Which GitLab token to create',
 'Create a classic personal access token with read_api. A fine-grained token needs Metadata Read plus Projects Read plus Code Read, and short of all three every call returns 403 insufficient_granular_scope - which reads exactly like an expired token and is not. GITLAB_MCP_AUTH_TOKEN is a different thing entirely: it is a shared secret gating the sidecar HTTP endpoint, not a GitLab credential.',
 'kate', 'integration', now() - interval '16 days'),

('Sessions are cookie-based, and the hash is not argon2',
 'Login sets an httpOnly, SameSite=Lax cookie holding a random token; the database stores only its SHA-256. A session token is 256 bits of entropy from the CSPRNG, so there is nothing to brute force and a slow hash would only add latency to every authenticated request. Passwords are a different case and do use argon2, because a password is low-entropy by nature. Using the same primitive for both would be wrong in one direction or the other.',
 'dmytro', 'security', now() - interval '18 days'),

('CSRF protection is double-submit',
 'Because the session lives in a cookie, any page on the internet can cause the browser to send it. Every mutating request must carry an X-CSRF-Token header matching a second, non-httpOnly cookie. GET is exempt and must therefore stay side-effect free - a GET that mutates is not merely unRESTful here, it is the hole.',
 'dmytro', 'security', now() - interval '17 days'),

('One unreachable source degrades the list, never empties it',
 'The federation gathers from every enabled source concurrently and isolates failures structurally: a connector that raises is caught at its own boundary and reported as a failed source with its error text, while the others still return. The API answers 200 with fewer hits, not 502. This is the behaviour the Sources page exists to make visible - a source that is down says so, rather than quietly contributing nothing.',
 'dmytro', 'architecture', now() - interval '4 days'),

('Citations are verified before they are rendered',
 'The answer model is told to cite with bracketed numbers. Each number is resolved against the hits that were actually in the prompt: a number in range becomes a link to that card, and one outside it is dropped, counted, and rendered as plain text. The UI reports how many were used and how many were discarded. The model cannot manufacture a source, because the frontend has no way to link to something that was never sent.',
 'anna', 'architecture', now() - interval '7 days'),

('Answers are Markdown, and are rendered as Markdown',
 'Models answer with headings, lists, tables and fenced code. Those are rendered with react-markdown rather than compiled as MDX, for two reasons. MDX evaluates what it parses, so an answer containing a JSON object is read as a JSX expression and crashes the page. And the answer streams token by token into a client component, where there is no server render in which to compile it. Raw HTML in model output is escaped, not executed.',
 'anna', 'frontend', now() - interval '1 day'),

('Next.js 16 renamed middleware and changed the async rules',
 'middleware.ts is now proxy.ts, cookies() and params are async only, and rewrites() land in the afterFiles phase. Streaming the search response needs a Route Handler; a rewrite cannot express it because the response has to stay unbuffered all the way to the browser. An upgrade that only renames the file will typecheck and then fail at runtime on the first synchronous cookies() call.',
 'kate', 'frontend', now() - interval '10 days');

INSERT INTO tickets (title, body, status, reporter, component, created_at) VALUES
('vLLM hangs at "vLLM is using nccl"',
 'Startup stalls with no further output after the nccl banner. Both times this happened the cause was two model configs assigned overlapping GPU sets, so the second launch waited forever for devices the first one held. Check nvidia-smi before assuming a network problem.',
 'closed', 'anna', 'inference', now() - interval '14 days'),

('Grafana generation-rate panel always reads 10 tokens/s',
 'The panel used histogram_quantile over a histogram with default buckets, whose highest finite bucket is 10 - so every value above that clamps to exactly 10. Fixed by declaring explicit buckets on the output-tokens-per-second histogram.',
 'closed', 'dmytro', 'observability', now() - interval '9 days'),

('Search returns nothing when GitLab is down',
 'One unreachable source should degrade the result list, not empty it. Expected behaviour is HTTP 200 with the other source hits present and an error recorded against the failing one.',
 'closed', 'kate', 'search', now() - interval '2 days'),

('Context indicator in opencode shows the wrong window',
 'opencode reads limit.context from its own config and never asks the server, so the number has to be written down client-side and match --max-model-len. Too low and it compacts early; too high and it overruns the window mid-session.',
 'closed', 'kate', 'client', now() - interval '25 days'),

('Duplicate NVIDIA apt sources break every apt command',
 'Installing cuda-keyring adds a second entry for a repo the image already had, and apt refuses to choose between conflicting Signed-By values. Every apt invocation fails after that, including the purge you would use to fix it. Remove the older .list file before installing the keyring, not after.',
 'open', 'anna', 'infrastructure', now() - interval '1 day'),

('GitLab search returns 403 Host header is not allowed',
 'This reads exactly like an authentication failure and is not one. The sidecar applies DNS-rebinding protection and rejects any Host it was not told to expect, before it ever looks at the token. Set MCP_ALLOWED_HOSTS to the host the API actually dials. An hour went into rotating a perfectly good token over this.',
 'closed', 'dmytro', 'integration', now() - interval '12 days'),

('unhandled errors in a TaskGroup, with no cause',
 'Every MCP failure surfaced as this one opaque string. The SDK raises inside an anyio task group, which wraps whatever went wrong in an ExceptionGroup, and the group message says nothing about its members. Fixed by flattening the group and reporting the leaf exceptions, so a refused connection now says so.',
 'closed', 'dmytro', 'integration', now() - interval '15 days'),

('Recency boost outranks relevance',
 'A hit from yesterday with a poor match was landing above an excellent match from last month. The boost was 0.15 - larger than the RRF gap across nine rank positions - so it was not a tiebreak, it was the ranking. Reduced to 0.016 and covered by a test that asserts the bound directly.',
 'closed', 'dmytro', 'search', now() - interval '5 days'),

('Drive excerpts always start at the beginning of the document',
 'A search for a term appearing on page four returned the first 400 characters of the document, which never contained it - so every card looked irrelevant even when the hit was right. The excerpt is now centred on the matched passage, with a short lead-in so the sentence is readable.',
 'closed', 'kate', 'search', now() - interval '4 days'),

('Uppercase styling destroys case-sensitive data',
 'The restyle applied uppercase to card titles, which turned a file path into TEST/TEST/SEARCH/FEDERATION.PY and made it useless for finding the file. Chrome and labels stay uppercase; anything that came out of a source keeps its own casing.',
 'closed', 'kate', 'frontend', now() - interval '6 days'),

('Python 3.14 has no wheels for half the stack',
 'pydantic-core, greenlet and asyncpg all tried to build from source, and SQLAlchemy 2.0.36 crashed at import on 3.14 with a Union subscript error. Resolved by moving to sqlalchemy 2.0.52, greenlet 3.5.5, asyncpg 0.31.0 and pydantic 2.13.4 rather than pinning the interpreter back.',
 'closed', 'anna', 'backend', now() - interval '22 days'),

('mcp SDK and FastAPI disagree about Starlette',
 'Installing mcp 1.29.0 pulled Starlette 1.6.0, which fastapi 0.115.6 refuses. There is no version pair that satisfies both at those pins; fastapi had to move to 0.141.1. Anyone who tells you the two are independent has not tried it.',
 'closed', 'dmytro', 'backend', now() - interval '21 days'),

('Sources page shows a source as healthy when it has no usable tools',
 'Reachability and usefulness are different questions. A server can accept a connection, list its tools, and still not expose the one the connector needs. The check now records which tools were found and flags the missing one, instead of reporting a green dot.',
 'open', 'kate', 'search', now() - interval '8 days'),

('Generated SQL is rejected when the search term contains a semicolon',
 'The validator counted statements before masking string literals, so searching for a term with a semicolon or the words DROP TABLE looked like an injection attempt. Literals are now blanked before any of the checks run, which is both correct and the only way those searches can work at all.',
 'closed', 'dmytro', 'search', now() - interval '19 days'),

('Answer cites a source number that does not exist',
 'The model emitted a bracketed 9 when only six hits were in the prompt. Nothing crashed, but the number rendered as a link to nowhere. Out-of-range citations are now dropped, counted, and shown as plain text, and the count appears next to the answer.',
 'closed', 'anna', 'search', now() - interval '7 days'),

('Postgres source disappears whenever the model is unavailable',
 'If text2sql cannot reach the answer endpoint, or the model returns something that fails validation, this source contributed nothing and the demo looked broken. There is now a deterministic ILIKE fallback that needs no model at all, and the UI shows which of the two produced the query.',
 'closed', 'anna', 'search', now() - interval '11 days');

INSERT INTO runbooks (title, body, owner, system, updated_at) VALUES
('Bringing the demo stack up from nothing',
 'Copy .env.example to .env and set POSTGRES_PASSWORD and USE_MOCK_VLLM=true. Run docker compose --profile dev up -d --build, which adds the mock answer model so no GPU is needed. Create an account with manage.py create-user. Open the web app on port 3001 - Grafana already owns 3000, which is why. Migrations run on API startup, so there is no separate migrate step.',
 'anna', 'platform', now() - interval '2 days'),

('Reseeding the knowledge-base corpus',
 'The initdb scripts run only against an empty volume, so on an existing stack pipe them through psql by hand. 01-create-kb.sql creates the database and the kb_ro role and needs to run once; 02-seed-kb.sql drops and recreates the four demo tables and can be run as often as you like. Re-run the GRANT at the end of the seed if you add a table, because a new table is not covered by an earlier grant.',
 'dmytro', 'platform', now() - interval '1 day'),

('Rotating a leaked GitLab token',
 'Revoke it first, at user settings then personal access tokens - a token that is still valid while you edit configuration is the whole problem. Issue a classic replacement with read_api, put it in .env, and restart the gitlab-mcp container so it re-reads the environment. Check the Sources page afterwards: a stale token shows as 401, a wrong Host as 403.',
 'kate', 'integration', now() - interval '12 days'),

('Recovering from the duplicate NVIDIA apt source',
 'Symptom is that every apt command fails over conflicting Signed-By values, including the purge that would fix it. Delete the older /etc/apt/sources.list.d entry for the CUDA repo directly, then run apt update. Do not try to remove the keyring package first; the removal itself needs a working apt.',
 'anna', 'inference', now() - interval '9 days'),

('Restarting inference after an OOM',
 'Confirm with nvidia-smi that no process still holds memory - a killed worker can leave the allocation behind. Check that no second config claims the same GPUs, which is the usual cause of a silent hang at the nccl banner rather than an error. Then relaunch with ulimit -n 65535 in the launching shell, and watch for the ZMQ timeout that means the descriptor limit did not take.',
 'anna', 'inference', now() - interval '14 days'),

('Verifying the read-only boundary holds',
 'Do not take the configuration on trust; prove it. Connect as kb_ro to the kb database and select from users - it must fail with relation does not exist, because the application tables live in another database entirely. Then try to connect kb_ro to that other database, which must be refused outright. If either succeeds, the SQL guardrails are decorative and the source has to be disabled until it is fixed.',
 'dmytro', 'security', now() - interval '3 days'),

('Completing the Google OAuth flow',
 'Consent needs a browser, which the container does not have, so the flow is run once on a workstation and the resulting tokens are mounted in. Keep them out of the image and out of git - they live in the gitignored secrets directory. The server is behind an opt-in compose profile precisely because this step cannot be automated.',
 'kate', 'integration', now() - interval '17 days'),

('Checking a source without using the UI',
 'POST to the source check endpoint with the session cookie and the CSRF header. It asks the server for its tool list and stores the answer, which is how you discover that a GitLab instance has no code search or that a toolset was never enabled. Configuration cannot tell you this; only the server can.',
 'dmytro', 'platform', now() - interval '6 days');

INSERT INTO decisions (title, body, decided_by, status, decided_at) VALUES
('Rank with reciprocal rank fusion, not normalised scores',
 'Considered normalising each source score into a shared range. Rejected: two of the three sources return no score at all, so the normalisation would have been invented for them, and a fabricated score is worse than none. RRF uses only rank, which every source genuinely has. Accepted with source weights as the single tuning knob.',
 'dmytro', 'accepted', now() - interval '28 days'),

('Put the knowledge base in its own database',
 'Considered a schema inside the application database with table-level grants. Rejected: a role that can connect can still enumerate schemas and table names, and generated SQL is reachable by anyone who can get text into an indexed document. A separate database with CONNECT revoked makes the application tables unreachable rather than merely unreadable.',
 'dmytro', 'accepted', now() - interval '26 days'),

('Treat the LLM endpoint as the answer engine',
 'The service began as a metrics proxy in front of a vLLM pod. Rather than keep the model at arms length, it now reads the fused results and writes the answer over them. The proxy stays for opencode and is untouched. Accepted on the condition that search never depends on it: with no endpoint registered at all, the hits still come back.',
 'anna', 'accepted', now() - interval '24 days'),

('Cookie sessions rather than JWT',
 'Considered stateless tokens. Rejected: logout and revocation are the whole point of a session here, and a stateless token cannot be revoked without the server-side state that was supposed to be avoided. A row per session also gives an honest list of active devices. Accepted with SameSite=Lax and double-submit CSRF.',
 'dmytro', 'accepted', now() - interval '23 days'),

('Render answers as Markdown rather than MDX',
 'Considered compiling model output with the same MDX pipeline the site uses for its writing. Rejected on two grounds: MDX evaluates what it parses, so an answer containing braces or an angle bracket is read as JSX and crashes; and the answer streams into a client component where no server compile step exists. The styling is shared; the parser is not.',
 'anna', 'accepted', now() - interval '1 day'),

('Keep a deterministic fallback for SQL generation',
 'Considered failing the source when the model is unavailable. Rejected: a demo where one source intermittently vanishes reads as a broken integration rather than a degraded one. A plain ILIKE query needs no model, and the UI shows which of the two produced the result, so the difference is visible rather than hidden.',
 'anna', 'accepted', now() - interval '20 days'),

('Do not add an embedding index yet',
 'Considered pgvector over the corpus for semantic retrieval. Deferred: the corpus is small enough that lexical matching is not the bottleneck, and the interesting problem in this project is fusing several sources rather than improving any one of them. Revisit when a single source is demonstrably the weak link.',
 'dmytro', 'deferred', now() - interval '15 days'),

('Isolate connector failures structurally, not by convention',
 'Considered documenting that connectors must not raise. Rejected: a convention holds until the first connector that forgets, and then one dead source empties the whole result list. The contract is enforced at the boundary instead - every connector is awaited inside its own guard, and a raised exception becomes a reported source error.',
 'dmytro', 'accepted', now() - interval '18 days');

CREATE INDEX ix_articles_updated_at ON articles (updated_at DESC);
CREATE INDEX ix_tickets_created_at ON tickets (created_at DESC);
CREATE INDEX ix_runbooks_updated_at ON runbooks (updated_at DESC);
CREATE INDEX ix_decisions_decided_at ON decisions (decided_at DESC);

-- Read-only, and only on what exists right now. A table added later needs
-- an explicit grant, which is the safe default for a role reachable by
-- generated SQL - and the reason this line has to be re-run after adding
-- one, rather than being a one-time setup step.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO kb_ro;
