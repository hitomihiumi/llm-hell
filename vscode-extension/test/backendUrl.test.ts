import assert from "node:assert/strict";
import { test } from "node:test";
import { describeBackend, normaliseBackendUrl, rememberBackend } from "../src/backendUrl.ts";

// --- normaliseBackendUrl ---------------------------------------------------------

test("a full URL is kept as it is", () => {
  assert.equal(normaliseBackendUrl("https://llmhell.borzo.ai"), "https://llmhell.borzo.ai");
});

test("a bare host:port is what somebody means, not an error", () => {
  /* Matches `withScheme` in http.ts, which the client has always accepted -
     the picker must not reject what the setting already allows. */
  assert.equal(normaliseBackendUrl("localhost:8000"), "http://localhost:8000");
});

test("a trailing slash is dropped, so one backend is not two entries", () => {
  assert.equal(normaliseBackendUrl("https://example.com/"), "https://example.com");
  assert.equal(normaliseBackendUrl("https://example.com///"), "https://example.com");
});

test("surrounding whitespace from a paste is ignored", () => {
  assert.equal(normaliseBackendUrl("  https://example.com  "), "https://example.com");
});

test("a URL with a path is refused rather than silently corrupting every request", () => {
  /* API routes are appended to this. A pasted page URL would not fail once,
     visibly - it would prefix every call for the rest of the session. */
  assert.equal(normaliseBackendUrl("https://example.com/api/search"), undefined);
  assert.equal(normaliseBackendUrl("https://example.com?x=1"), undefined);
  assert.equal(normaliseBackendUrl("https://example.com#top"), undefined);
});

test("nothing at all is nothing, not a default", () => {
  assert.equal(normaliseBackendUrl(""), undefined);
  assert.equal(normaliseBackendUrl("   "), undefined);
});

test("something that is not a URL is refused", () => {
  assert.equal(normaliseBackendUrl("http://"), undefined);
});

test("a port on its own is not a host", () => {
  assert.equal(normaliseBackendUrl(":8000"), undefined);
});

// --- describeBackend -------------------------------------------------------------

test("the host is what identifies a backend at a glance", () => {
  assert.equal(describeBackend("https://llmhell.borzo.ai"), "llmhell.borzo.ai");
  assert.equal(describeBackend("http://localhost:8000"), "localhost:8000");
});

test("something unparseable is shown as typed rather than hidden", () => {
  assert.equal(describeBackend("not a url"), "not a url");
});

// --- rememberBackend -------------------------------------------------------------

test("the one just used comes first", () => {
  assert.deepEqual(rememberBackend(["a", "b"], "c"), ["c", "a", "b"]);
});

test("using one already in the list moves it up rather than duplicating it", () => {
  /* Switching between two backends is the case this exists for: the one you
     just left has to be the next one offered. */
  assert.deepEqual(rememberBackend(["a", "b", "c"], "c"), ["c", "a", "b"]);
});

test("the list is capped", () => {
  const many = ["1", "2", "3", "4", "5", "6", "7"];
  assert.equal(rememberBackend(many, "new", 3).length, 3);
  assert.deepEqual(rememberBackend(many, "new", 3), ["new", "1", "2"]);
});
