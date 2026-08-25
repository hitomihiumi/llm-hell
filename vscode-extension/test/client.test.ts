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
let seen: { method: string; path: string; cookie: string; csrf: string }[] = [];
/** Flipped by a test to make an established session look expired. */
let sessionsValid = true;

before(async () => {
  server = createServer((request, response) => {
    const path = request.url ?? "";
    const cookie = request.headers.cookie ?? "";
    const csrf = String(request.headers["x-csrf-token"] ?? "");
    seen.push({ method: request.method ?? "", path, cookie, csrf });

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

    const signedIn = sessionsValid && cookie.includes("kb_session=session-value");
    if (!signedIn) {
      send(401, { detail: "Not authenticated" });
      return;
    }
    // The API's double-submit check, which exempts safe methods.
    if (request.method !== "GET" && csrf !== "csrf-value") {
      send(403, { detail: "CSRF token missing or invalid" });
      return;
    }

    if (path === "/api/search") {
      send(200, { query_id: "q1", query: "x", queries: [], hits: [], source_status: [] });
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
    if (path === "/api/sources") {
      send(200, [{ key: "gitlab", display_name: "GitLab", enabled: true }]);
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

function client(stored?: string) {
  const secrets: SecretStore = {
    get: async () => stored,
    store: async () => undefined,
    delete: async () => undefined,
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

  await kb.sources();

  const sources = seen.find((request) => request.path === "/api/sources");
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

  await kb.sources();

  assert.deepEqual(
    seen.map((request) => request.path),
    ["/api/auth/login", "/api/sources"],
  );
});

test("an expired session is renewed and the request retried once", async () => {
  /* Sessions last a week. An editor left open over a weekend should not
     answer a search with "not authenticated" when it holds the credentials
     to fix that itself. */
  fresh();
  const kb = client(PASSWORD);
  await kb.sources();

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

  await assert.rejects(() => kb.sources(), AuthError);
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

  await assert.rejects(() => kb.sources(), /Could not reach http:\/\/127\.0\.0\.1:1/);
});

test("a stored password counts as signed in across a restart", async () => {
  /* A session lives in memory, so a restarted editor has none - and the view
     would offer "Sign in" to somebody who already had. */
  fresh();

  assert.equal(await client(PASSWORD).hasCredentials(), true);
  assert.equal(await client().hasCredentials(), false);
});
