# Searching as yourself

Signing in to the knowledge base says who you are. It does not say what you
may read: Drive belongs to Google and the repositories belong to a GitLab
instance, and both want their own consent. Until this existed every search ran
on one set of deployment-wide tokens, so twenty people searching meant twenty
people reading one person's Drive.

Now a user can connect their own accounts from the editor —
**Knowledge Base: Connect Accounts** — and their searches run as them.

---

## Turning it on

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```
CREDENTIALS_ENCRYPTION_KEY=<that value>
```

Empty is a supported state, not a broken one: per-user credentials switch off
entirely and every search uses the deployment's own tokens, which is what a
single-person install wants. The extension says so rather than showing a
button that quietly does nothing.

**Rotating the key does not migrate anything.** Every stored credential
becomes unreadable, and each user is asked to connect again. That is a
deliberate trade — the alternative is a key the database can help you recover,
which is not a key.

---

## What is stored, and what is not

| | |
| --- | --- |
| encrypted, never returned by any route | the GitLab token; the Google address the Workspace server's own credentials are filed under |
| plain, and shown in the interface | which account it speaks for, when it expires, which scopes it carries |

The response model for these routes has no field a secret could go in, which
is why "no route leaks a token" is a property of the shape rather than a note
in a review. What lands in the column is Fernet ciphertext: a dump that greps
for `glpat-` finds nothing, and two users with the same token do not have the
same row.

A key that has been rotated costs the credential and never the search — the
connector falls back to the deployment's token and logs why.

---

## GitLab

The user pastes a personal access token with `read_api`. The backend verifies
it against the instance **before** storing it, asking both who it belongs to
and whether it can list projects, because a token that passes `/user` and
fails everything else would otherwise surface days later as a source that
silently returns nothing.

### The setting that makes it work at all

`REMOTE_AUTHORIZATION=true` on the `gitlab-mcp` container, and this is not
optional. In its default mode the server reads a `private-token` header off
each request **and ignores it**, using its own environment PAT for everybody.
Measured before the setting was added: a deliberately wrong token still
returned the same two hits, which is what per-user auth failing silently looks
like.

There is no middle setting. With `REMOTE_AUTHORIZATION` on, a request carrying
no token gets no GitLab access at all — `buildAuthHeaders` returns `{}` — so
the fallback lives in the connector instead: the caller's token when they
connected one, the deployment's when they did not. The MCP container is no
longer given a PAT, because it would be dead configuration that looks live.

Verified three ways, and the third is the one that matters:

| | |
| --- | --- |
| no per-user token | 2 hits — the deployment's token |
| the user's own token | 2 hits |
| a deliberately wrong token | **401, 0 hits** |

---

## Google

Per-user Google works by *addressing* the account: the Workspace MCP server is
multi-account, every tool takes an `email`, and a search runs as whichever
address the caller connected.

**The consent flow cannot be driven from the container, and that is the
server's limit rather than a missing feature here.** `manage_accounts` has an
`authenticate` operation whose own description says "opens browser" — in a
container it spawns one, waits for a local OAuth callback, and never returns.
Measured: the call hung until the 25-second tool timeout, emitting no URL at
any point.

So the extension asks the question that can be answered — does the server hold
working credentials for this address — and, when it does not, shows what to do
about it. Adding an account is the same workstation procedure
`docs/google-workspace-setup.md` describes for the first one. Once it appears,
connecting it in the editor takes one step and searches run as it.

Reading the answer to that question turned out to be where the bugs were.
Both were found by probing a live server rather than by review:

- the first version asked whether the report contained `valid`, which is true
  of `invalid` too, so an address that had never authenticated reported as
  connected;
- and it looked for full `https://www.googleapis.com/auth/...` scope URLs,
  while the server prints short names — so twelve scopes read as none.

Both are now parsed from the report's checkboxes and bullet list, with the
real text of both cases pinned in `tests/test_google_workspace.py`.
