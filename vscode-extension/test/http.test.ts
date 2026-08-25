import assert from "node:assert/strict";
import { test } from "node:test";
import { parseCookies, readSetCookie, withScheme } from "../src/http.ts";

/**
 * The cookie handling, because the extension host's `fetch` has no jar and
 * this is the part that would fail silently: a session cookie that is not
 * kept produces "not authenticated" on the next request, which reads like a
 * credentials problem rather than a parsing one.
 */

test("both cookies are read from a repeated Set-Cookie header", () => {
  const lines = readSetCookie({
    headers: {
      getSetCookie: () => [
        "kb_session=abc123; HttpOnly; Path=/; SameSite=Lax",
        "kb_csrf=xyz789; Path=/; SameSite=Lax",
      ],
      get: () => null,
    },
  });

  assert.equal(lines.length, 2);
});

test("a runtime without getSetCookie still yields the header it has", () => {
  const lines = readSetCookie({
    headers: { get: (name: string) => (name === "set-cookie" ? "kb_session=abc" : null) },
  });

  assert.deepEqual(lines, ["kb_session=abc"]);
});

test("no Set-Cookie is not an error", () => {
  assert.deepEqual(readSetCookie({ headers: { get: () => null } }), []);
});

test("a host typed without a scheme is what someone meant", () => {
  assert.equal(withScheme("localhost:8000"), "http://localhost:8000");
  assert.equal(withScheme("https://kb.example.com"), "https://kb.example.com");
});

test("a trailing slash does not become a double slash in a path", () => {
  assert.equal(withScheme("http://localhost:8000/"), "http://localhost:8000");
  assert.equal(
    new URL("/api/search", withScheme("http://localhost:8000/")).pathname,
    "/api/search",
  );
});

test("only the cookies the API sets are kept", () => {
  const cookies = parseCookies(
    ["kb_session=abc; HttpOnly", "kb_csrf=xyz", "_ga=tracking; Path=/"],
    ["kb_session", "kb_csrf"],
  );

  assert.deepEqual([...cookies.keys()], ["kb_session", "kb_csrf"]);
  assert.equal(cookies.get("kb_session"), "abc");
});

test("a value containing an equals sign survives", () => {
  const cookies = parseCookies(["kb_session=a=b=c; Path=/"], ["kb_session"]);

  assert.equal(cookies.get("kb_session"), "a=b=c");
});
