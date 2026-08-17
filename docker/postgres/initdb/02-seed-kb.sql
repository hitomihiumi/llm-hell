-- Demo corpus for the Postgres source.
--
-- Deliberately prose-heavy and overlapping with the kind of thing that
-- would live in Drive or a GitLab wiki, because the point of the demo is
-- that one question pulls related material out of several systems at once.

\connect kb

CREATE TABLE articles (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    author      TEXT,
    category    TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tickets (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    reporter    TEXT,
    component   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO articles (title, body, author, category, updated_at) VALUES
('Deploying the inference stack to RunPod',
 'The inference stack runs on 4x RTX PRO 6000 Blackwell GPUs. Start it with tools/run_deepseek.sh, which installs the CUDA toolkit, creates a uv virtualenv, downloads the checkpoint and launches vLLM with data-parallel 4 and expert parallelism. The single most common failure is the open-file limit: the data-parallel coordinator opens a socket per rank, and at the default 1024 descriptors it dies before it can report anything, leaving only a ZMQ timeout in the log.',
 'anna', 'infrastructure', now() - interval '3 days'),
('KV cache must be fp8 on DeepSeek V4',
 'DeepSeek V4 uses sparse MLA attention, which only implements an fp8 cache layout. Passing anything else makes layer construction assert during load_model, before any cache is allocated, so there is no unquantized fallback. This is not a memory optimisation and cannot be traded away for accuracy.',
 'anna', 'infrastructure', now() - interval '11 days'),
('How search federation ranks results',
 'Results from Drive, GitLab and Postgres carry relevance scores that are not comparable, and two of the three often carry none at all. We therefore rank by reciprocal rank fusion: each hit contributes weight / (60 + rank within its own source). This ignores the sources own scores entirely, on purpose - treating them as commensurate would silently favour whichever backend happens to return the largest numbers.',
 'dmytro', 'architecture', now() - interval '1 day'),
('Onboarding: getting access to the knowledge base',
 'Ask an admin to run manage.py create-user with a password. Accounts created before the web app existed have no password hash and cannot log in until manage.py set-password is run for them; they keep working against the API-key proxy in the meantime.',
 'kate', 'onboarding', now() - interval '20 days'),
('Why generated SQL runs as a read-only role',
 'Search over Postgres works by asking the model to write a SELECT. The prompt says SELECT only and a validator rejects anything else, but neither of those is the actual control: the sidecar connects as kb_ro, against a database that does not contain the application tables, with default_transaction_read_only and a five second statement timeout. A prompt injection in an indexed document that gets the model to emit a DELETE simply fails.',
 'dmytro', 'security', now() - interval '2 days'),
('Reasoning effort is chosen by the client',
 'opencode has its own effort selector and sends reasoning_effort on the request. The proxy forwards it untouched. It used to overwrite the value from a suffix on the model id, which meant the selector appeared to do nothing at all.',
 'anna', 'architecture', now() - interval '30 days');

INSERT INTO tickets (title, body, status, reporter, component, created_at) VALUES
('vLLM hangs at "vLLM is using nccl"',
 'Startup stalls with no further output after the nccl banner. Both times this happened the cause was two model configs assigned overlapping GPU sets, so the second launch waited forever for devices the first one held. Check nvidia-smi before assuming a network problem.',
 'closed', 'anna', 'inference', now() - interval '14 days'),
('Grafana generation-rate panel always reads 10 tokens/s',
 'The panel used histogram_quantile over a histogram with default buckets, whose highest finite bucket is 10 - so every value above that clamps to exactly 10. Fixed by declaring explicit buckets on the output-tokens-per-second histogram.',
 'closed', 'dmytro', 'observability', now() - interval '9 days'),
('Search returns nothing when GitLab is down',
 'One unreachable source should degrade the result list, not empty it. Expected behaviour is HTTP 200 with the other sources hits present and an error recorded against the failing one.',
 'open', 'kate', 'search', now() - interval '2 days'),
('Context indicator in opencode shows the wrong window',
 'opencode reads limit.context from its own config and never asks the server, so the number has to be written down client-side and match --max-model-len. Too low and it compacts early; too high and it overruns the window mid-session.',
 'closed', 'kate', 'client', now() - interval '25 days'),
('Duplicate NVIDIA apt sources break every apt command',
 'Installing cuda-keyring adds a second entry for a repo the image already had, and apt refuses to choose between conflicting Signed-By values. Every apt invocation fails after that, including the purge you would use to fix it.',
 'open', 'anna', 'infrastructure', now() - interval '1 day');

CREATE INDEX ix_articles_updated_at ON articles (updated_at DESC);
CREATE INDEX ix_tickets_created_at ON tickets (created_at DESC);

-- Read-only, and only on what exists right now. A table added later needs
-- an explicit grant, which is the safe default for a role reachable by
-- generated SQL.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO kb_ro;
