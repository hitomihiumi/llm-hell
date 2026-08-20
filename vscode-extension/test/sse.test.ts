import assert from "node:assert/strict";
import { test } from "node:test";
import { SseParser } from "../src/sse.ts";

/**
 * The framing, which is where a streaming client goes wrong.
 *
 * Chunks arrive on network boundaries rather than message ones, so the
 * failure mode is not an exception — it is an answer that comes out with
 * words missing, or a `done` that never fires and leaves the turn spinning.
 * Neither is visible without looking closely, which is why it is tested.
 */

const HITS = 'event: hits\ndata: {"hits":[],"source_status":[]}\n\n';
const TOKEN = 'event: token\ndata: {"text":"Hello"}\n\n';

test("a whole event is read", () => {
  const events = new SseParser().push(HITS);

  assert.equal(events.length, 1);
  assert.equal(events[0].event, "hits");
  assert.deepEqual(events[0].data, { hits: [], source_status: [] });
});

test("several events in one chunk all come out, in order", () => {
  const events = new SseParser().push(HITS + TOKEN);

  assert.deepEqual(
    events.map((event) => event.event),
    ["hits", "token"],
  );
});

test("an event split across chunks is not lost", () => {
  const parser = new SseParser();

  assert.deepEqual(parser.push('event: token\ndata: {"te'), []);
  const events = parser.push('xt":"Hello"}\n\n');

  assert.deepEqual(events[0].data, { text: "Hello" });
});

test("a chunk boundary inside the blank line still terminates the event", () => {
  const parser = new SseParser();

  assert.deepEqual(parser.push(`${TOKEN.slice(0, -1)}`), []);
  assert.equal(parser.push("\n").length, 1);
});

test("tokens are emitted one at a time, in the order sent", () => {
  /* This is what makes the answer type itself out rather than appear. */
  const parser = new SseParser();
  const words: string[] = [];

  for (const word of ["The ", "team ", "object"]) {
    for (const event of parser.push(`event: token\ndata: {"text":"${word}"}\n\n`)) {
      words.push((event.data as { text: string }).text);
    }
  }

  assert.equal(words.join(""), "The team object");
});

test("a stream that ends without its final blank line keeps the last event", () => {
  /* Losing it would most often mean losing `done`, and the turn would look
     like it never finished. */
  const parser = new SseParser();
  parser.push('event: done\ndata: {"duration_ms":900}');

  const events = parser.flush();

  assert.equal(events[0].event, "done");
});

test("nothing left over is nothing to flush", () => {
  const parser = new SseParser();
  parser.push(TOKEN);

  assert.deepEqual(parser.flush(), []);
});

test("keep-alive comments are ignored rather than parsed", () => {
  assert.deepEqual(new SseParser().push(": keep-alive\n\n"), []);
});

test("CRLF is accepted, because a proxy may rewrite the endings", () => {
  const events = new SseParser().push('event: token\r\ndata: {"text":"x"}\r\n\r\n');

  assert.equal(events.length, 1);
  assert.deepEqual(events[0].data, { text: "x" });
});

test("data that is not JSON is handed back as text, not dropped", () => {
  const events = new SseParser().push("event: error\ndata: upstream exploded\n\n");

  assert.equal(events[0].data, "upstream exploded");
});

test("an event with no data is not an event", () => {
  assert.deepEqual(new SseParser().push("event: ping\n\n"), []);
});
