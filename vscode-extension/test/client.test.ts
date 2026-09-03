import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";
import { ApiError, AuthError, KnowledgeBaseClient, type SecretStore } from "../src/client.ts";

/**
 * The client, driven against a stand-in for the API.
 *
 * The stand-in enforces exactly what the real backend enforces — session
 * cookie, and the CSRF token echoed back in a header on anything that is not
 * a GET — because those are the two rules this client has to satisfy without
 * a browser to satisfy them for it. Getting either wrong produces "Not
 * authenticated" or a 403 at runtime, in an extension host, where it is
 * tedious to see and easy to misread as a credentials problem.
 */

const USERNAME = "demo";
const PASSWORD = "correct horse";

let server: Server;
let baseUrl: string;
/** Every request the stub saw, so a test can assert on what was sent. */
let seen: {
  method: string;
  path: string;
  cookie: string;
  csrf: string;
  authorization: string;
}[] = [];
const VALID_KEY = "llmhell_testkey";
/** Flipped by a test to make an established session look expired. */
let sessionsValid = true;

before(async () => {
  server = createServer((request, response) => {
    const path = request.url ?? "";
    const cookie = request.headers.cookie ?? "";
    const csrf = String(request.headers["x-csrf-token"] ?? "");
    const authorization = String(request.headers.authorization ?? "");
    seen.push({ method: request.method ?? "", path, cookie, csrf, authorization });

    const send = (status: number, body: unknown, headers: Record<string, string[]> = {}) => {
      response.writeHead(status, { "content-type": "application/json", ...headers });
      response.end(JSON.stringify(body));
    };

    if (path === "/api/auth/login") {
      let raw = "";
      request.on("data", (chunk) => {
        raw += chunk;
      });
      request.on("end", () => {
        const body = JSON.parse(raw || "{}");
        if (body.username !== USERNAME || body.password !== PASSWORD) {
          send(401, { detail: "Invalid username or password" });
          return;
        }
        sessionsValid = true;
        send(
          200,
          { username: USERNAME },
          {
            "set-cookie": [
              "kb_session=session-value; HttpOnly; Path=/; SameSite=Lax; Expires=Wed, 21 Oct 2026 07:28:00 GMT",
              "kb_csrf=csrf-value; Path=/; SameSite=Lax",
            ],
          },
        );
      });
      return;
    }

    // `/v1` authenticates by bearer key, not by cookie - the same split the
    // real API has, where those routes take `CurrentKeyUser` and everything
    // under `/api` takes a session.
    const keyed = path.startsWith("/v1/");
    const signedIn = sessionsValid && cookie.includes("kb_session=session-value");
    if (!keyed && !signedIn) {
      send(401, { detail: "Not authenticated" });
      return;
    }
    // The API's double-submit check, which exempts safe methods - and bearer
    // requests, which carry no cookie to double-submit.
    if (!keyed && request.method !== "GET" && csrf !== "csrf-value") {
      send(403, { detail: "CSRF token missing or invalid" });
      return;
    }

    if (path === "/api/search") {
      send(200, { query_id: "q1", query: "x", queries: [], hits: [], source_status: [] });
      return;
    }
    if (path === "/v1/chat/completions") {
      // The exact key, not merely the shape of one: a prefix check would
      // accept a revoked or mistyped key and prove nothing.
      if (authorization !== `Bearer ${VALID_KEY}`) {
        send(401, { error: { message: "bad key" } });
        return;
      }
      response.writeHead(200, { "content-type": "text/event-stream" });
      response.write(
        'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"delta":{"content":"keyed"}}]}\n\n',
      );
      response.write("data: [DONE]\n\n");
      response.end();
      return;
    }
    if (path === "/api/chat/completions") {
      response.writeHead(200, { "content-type": "text/event-stream" });
      response.write(
        'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"delta":{"content":"hello"}}]}\n\n',
      );
      response.write(
        'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
      );
      response.write("data: [DONE]\n\n");
      response.end();
      return;
    }
    if (path === "/api/credentials") {
      send(200, [{ provider: "gitlab", connected: true }]);
      return;
    }
    if (path === "/api/content/gitlab%3Acode%3A3") {
      send(200, {
        hit_id: "gitlab:code:3",
        title: "main.go",
        text: "package main",
        language: "go",
        truncated: false,
        preview_pages: 0,
      });
      return;
    }
    send(404, { detail: "nothing to show for this result" });
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(() => {
  server.close();
});

/**
 * A secret store that actually keys on the name.
 *
 * It used to answer every `get` with the one stored value, which was true
 * enough while a password was the only secret there was - and became a lie
 * the moment an access key joined it, handing the password back as though it
 * were the key.
 */
function client(stored?: string, accessKey?: string) {
  const values = new Map<string, string>();
  if (stored) values.set("knowledgeBase.password", stored);
  if (accessKey) values.set("knowledgeBase.accessKey", accessKey);
  const secrets: SecretStore = {
    get: async (key: string) => values.get(key),
    store: async (key: string, value: string) => {
      values.set(key, value);
    },
    delete: async (key: string) => {
      values.delete(key);
    },
  };
  return new KnowledgeBaseClient(secrets, () => ({ baseUrl, username: USERNAME }));
}

function fresh() {
  seen = [];
  sessionsValid = true;
}

test("signing in keeps the session cookie", async () => {
  fresh();
  const kb = client();

  await kb.signIn({ username: USERNAME, password: PASSWORD });

  assert.equal(kb.signedIn, true);
});

test("a wrong password says so rather than failing later", async () => {
  fresh();
  const kb = client();

  await assert.rejects(() => kb.signIn({ username: USERNAME, password: "wrong" }), AuthError);
  assert.equal(kb.signedIn, false);
});

test("a search sends the session cookie and echoes the CSRF token", async () => {
  fresh();
  const kb = client(PASSWORD);

  await kb.search("where is the session cookie set", {});

  const search = seen.find((request) => request.path === "/api/search");
  assert.ok(search);
  assert.ok(search.cookie.includes("kb_session=session-value"));
  assert.equal(search.csrf, "csrf-value");
});

test("a GET is not given a CSRF header, which the API does not want", async () => {
  fresh();
  const kb = client(PASSWORD);

  await kb.credentials();

  const sources = seen.find((request) => request.path === "/api/credentials");
  assert.equal(sources?.csrf, "");
});

test("chat completions sends the session cookie and CSRF token", async () => {
  fresh();
  const kb = client(PASSWORD);

  const events = [];
  for await (const event of kb.chatCompletionsStream([{ role: "user", content: "hi" }])) {
    events.push(event);
  }

  const request = seen.find((request) => request.path === "/api/chat/completions");
  assert.ok(request);
  assert.equal(request.method, "POST");
  assert.ok(request.cookie.includes("kb_session=session-value"));
  assert.equal(request.csrf, "csrf-value");

  assert.equal(events.length, 3);
  const first = events[0].data as { choices?: [{ delta?: { content?: string } }] };
  assert.equal(first.choices?.[0]?.delta?.content, "hello");
});

test("a stored password signs in on the first request, with nothing asked", async () => {
  fresh();
  const kb = client(PASSWORD);

  await kb.credentials();

  assert.deepEqual(
    seen.map((request) => request.path),
    ["/api/auth/login", "/api/credentials"],
  );
});

test("an expired session is renewed and the request retried once", async () => {
  /* Sessions last a week. An editor left open over a weekend should not
     answer a search with "not authenticated" when it holds the credentials
     to fix that itself. */
  fresh();
  const kb = client(PASSWORD);
  await kb.credentials();

  sessionsValid = false;
  seen = [];
  await kb.search("still works", {});

  assert.deepEqual(
    seen.map((request) => request.path),
    ["/api/search", "/api/auth/login", "/api/search"],
  );
});

test("no stored password is an auth error, not a crash", async () => {
  fresh();
  const kb = client();

  await assert.rejects(() => kb.credentials(), AuthError);
});

test("content(id) fetches by a bare id, colon and all", async () => {
  /* read_knowledge_base_result only ever has an id string from a prior
     search - never a whole SearchHit - so this has to work from that alone. */
  fresh();
  const kb = client(PASSWORD);

  const content = await kb.content("gitlab:code:3");

  assert.equal(content.text, "package main");
});

test("a result with nothing to show surfaces its status, so the caller can fall back", async () => {
  fresh();
  const kb = client(PASSWORD);

  await assert.rejects(
    () => kb.content("google_mail:m1"),
    (error: unknown) => error instanceof ApiError && error.status === 404,
  );
});

test("an unreachable backend names the address rather than the stack", async () => {
  const secrets: SecretStore = {
    get: async () => PASSWORD,
    store: async () => undefined,
    delete: async () => undefined,
  };
  const kb = new KnowledgeBaseClient(secrets, () => ({
    baseUrl: "http://127.0.0.1:1",
    username: USERNAME,
  }));

  await assert.rejects(() => kb.credentials(), /Could not reach http:\/\/127\.0\.0\.1:1/);
});

test("a stored password counts as signed in across a restart", async () => {
  /* A session lives in memory, so a restarted editor has none - and the view
     would offer "Sign in" to somebody who already had. */
  fresh();

  assert.equal(await client(PASSWORD).hasCredentials(), true);
  assert.equal(await client().hasCredentials(), false);
});

// --- the access key ---------------------------------------------------------
//
// A key issued by `manage.py issue-key` grants the model through the backend's
// `/v1` routes, which reach the same coding provider the cookie route does. It
// does not grant the knowledge base, and the difference has to be visible
// rather than turning up as a mystery 401 two tools later.

const KEY = VALID_KEY;

test("with a key, chat completions goes to /v1 with a bearer and no cookie", async () => {
  fresh();
  const kb = client(PASSWORD, KEY);

  const events = [];
  for await (const event of kb.chatCompletionsStream([{ role: "user", content: "hi" }])) {
    events.push(event);
  }

  const request = seen.find((entry) => entry.path === "/v1/chat/completions");
  assert.ok(request, "the keyed route is the one used");
  assert.equal(request?.authorization, `Bearer ${KEY}`);
  assert.equal(request?.cookie, "", "a bearer request carries no session");
  assert.ok(!seen.some((entry) => entry.path === "/api/auth/login"), "no sign-in was needed");
  assert.equal(events.length, 2);
});

test("without a key, the cookie route is still the one used", async () => {
  fresh();
  const kb = client(PASSWORD);

  for await (const _ of kb.chatCompletionsStream([{ role: "user", content: "hi" }])) {
    // drained
  }

  assert.ok(seen.some((entry) => entry.path === "/api/chat/completions"));
  assert.ok(!seen.some((entry) => entry.path === "/v1/chat/completions"));
});

test("a key the backend refuses says so, rather than falling back", async () => {
  /* Silently signing in with the password instead would hide a key that has
     been revoked or mistyped. */
  fresh();
  const kb = client(PASSWORD, "llmhell_wrong-but-well-formed");

  await assert.rejects(
    async () => {
      for await (const _ of kb.chatCompletionsStream([{ role: "user", content: "hi" }])) {
        // never reached
      }
    },
    (error: Error) => error instanceof AuthError && /access key/.test(error.message),
  );
});

test("a key alone counts as being able to work", async () => {
  /* It grants the model, which is what the panel is for. Offering "sign in"
     to somebody whose coder answers would be the wrong thing to say. */
  const withKey = client(undefined, KEY);
  assert.equal(await withKey.hasCredentials(), true);

  const withNeither = client();
  assert.equal(await withNeither.hasCredentials(), false);
});

test("signing out takes the key with it", async () => {
  /* Leaving it behind would mean a panel that still works after saying it
     signed out. */
  fresh();
  const kb = client(PASSWORD, KEY);

  await kb.signOut();

  assert.equal(await kb.accessKey(), undefined);
  assert.equal(await kb.hasCredentials(), false);
});

test("the knowledge base explains itself when only a key is configured", async () => {
  /* The coder works and search does not; "Not signed in" alone would read as
     a bug to somebody watching the agent answer perfectly well. */
  fresh();
  const kb = client(undefined, KEY);

  await assert.rejects(
    () => kb.search("anything", {}),
    (error: Error) =>
      error instanceof AuthError && /grants the model, not search/.test(error.message),
  );
});
