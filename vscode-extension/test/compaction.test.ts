import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ATTACHMENT_MARKER,
  breakdown,
  type CompactableMessage,
  compact,
  estimateTokens,
  KEEP_RECENT,
  measure,
} from "../src/compaction.ts";

const say = (role: string, content: string): CompactableMessage => ({ role, content });
const toolResult = (size: number): CompactableMessage => ({
  role: "tool",
  tool_call_id: "c1",
  content: "x".repeat(size),
});

// --- estimateTokens --------------------------------------------------------------

test("an empty string costs nothing", () => {
  assert.equal(estimateTokens(""), 0);
});

test("ASCII is counted at about four characters a token", () => {
  assert.equal(estimateTokens("x".repeat(400)), 100);
});

test("Cyrillic is counted as costing more, because it does", () => {
  /* The four-characters-a-token rule is an English rule. Applying it to the
     Ukrainian these conversations are held in under-counts by half, which is
     the direction that ends in a refused request. */
  assert.ok(estimateTokens("а".repeat(400)) > estimateTokens("x".repeat(400)));
});

// --- when nothing needs doing -----------------------------------------------------

test("a conversation that fits is returned untouched", () => {
  const messages = [say("system", "env"), say("user", "hi"), say("assistant", "hello")];
  const result = compact(messages, 10_000);

  assert.equal(result.freed, 0);
  assert.equal(result.folded, 0);
  assert.equal(result.dropped, 0);
  assert.deepEqual(result.messages, messages);
});

test("a budget of zero means compaction is off, however long the conversation", () => {
  const messages = [say("system", "env"), toolResult(50_000)];
  assert.equal(compact(messages, 0).freed, 0);
});

// --- folding tool output ----------------------------------------------------------

test("an old tool result is folded to a stub, which is where the space is", () => {
  const messages = [
    say("system", "env"),
    say("user", "read it"),
    { role: "assistant", content: "", tool_calls: [{ id: "c1" }] },
    toolResult(40_000),
    ...Array.from({ length: KEEP_RECENT }, (_, i) => say("user", `later ${i}`)),
  ];

  const result = compact(messages, 2_000);

  assert.ok(result.folded >= 1);
  assert.ok(result.freed > 0);
  assert.ok(measure(result.messages) < measure(messages));
  assert.match(result.messages[3].content ?? "", /folded away/);
  assert.match(result.messages[3].content ?? "", /40000 characters/);
});

test("a folded result keeps its tool_call_id, or the exchange stops parsing", () => {
  /* A tool message answers a specific call. Losing the id would leave the
     model with a reply addressed to nothing. */
  const messages = [
    say("system", "env"),
    toolResult(40_000),
    ...Array.from({ length: KEEP_RECENT }, (_, i) => say("user", `later ${i}`)),
  ];

  const result = compact(messages, 1_000);

  assert.equal(result.messages[1].tool_call_id, "c1");
  assert.equal(result.messages[1].role, "tool");
});

test("a tool result too small to be worth folding is left alone", () => {
  /* Folding it would swap two characters for a sentence about two
     characters. The budget here is one that folding the big result alone
     satisfies, so nothing reaches the dropping stage. */
  const messages = [
    say("system", "env"),
    { role: "tool", tool_call_id: "small", content: "ok" },
    toolResult(40_000),
    ...Array.from({ length: KEEP_RECENT }, (_, i) => say("user", `later ${i}`)),
  ];

  const result = compact(messages, 3_000);

  assert.equal(result.dropped, 0);
  assert.equal(result.messages.find((m) => m.tool_call_id === "small")?.content, "ok");
  assert.match(result.messages.find((m) => m.tool_call_id === "c1")?.content ?? "", /folded away/);
});

// --- what is never touched --------------------------------------------------------

test("the leading system messages survive, whatever it costs", () => {
  /* They are the environment and the files the user attached: the turn is
     meaningless without them. */
  const messages = [
    say("system", "environment"),
    say("system", "attached files"),
    ...Array.from({ length: 40 }, (_, i) => say("user", "x".repeat(2_000) + i)),
  ];

  const result = compact(messages, 500);

  assert.equal(result.messages[0].content, "environment");
  assert.equal(result.messages[1].content, "attached files");
});

test("the most recent exchange survives, because it is what is being answered", () => {
  const messages = [
    say("system", "env"),
    ...Array.from({ length: 30 }, (_, i) => say("user", "x".repeat(2_000) + i)),
    say("user", "the live question"),
  ];

  const result = compact(messages, 500);

  assert.equal(result.messages.at(-1)?.content, "the live question");
});

// --- dropping, and saying so -------------------------------------------------------

test("when folding is not enough, the oldest exchanges go and a note says how many", () => {
  const messages = [
    say("system", "env"),
    ...Array.from({ length: 30 }, (_, i) => say("user", "x".repeat(4_000) + i)),
  ];

  const result = compact(messages, 1_000);

  assert.ok(result.dropped > 0);
  const note = result.messages.find((m) => /were dropped/.test(m.content ?? ""));
  assert.ok(note, "the drop is stated rather than silent");
  assert.match(note?.content ?? "", new RegExp(String(result.dropped)));
});

test("compaction goes under the budget, not merely to it", () => {
  /* Compacting to exactly the budget means compacting again on the next
     message; each pass has to buy room for several. */
  const messages = [
    say("system", "env"),
    ...Array.from({ length: 40 }, (_, i) => say("user", "x".repeat(2_000) + i)),
  ];

  const budget = 5_000;
  const result = compact(messages, budget);

  assert.ok(measure(result.messages) < budget * 0.85);
});

test("a conversation of nothing but recent messages is left alone rather than emptied", () => {
  /* Everything is protected here. Compaction has to give up rather than
     start eating the live exchange. */
  const messages = Array.from({ length: KEEP_RECENT }, () => say("user", "x".repeat(8_000)));

  const result = compact(messages, 100);

  assert.equal(result.messages.length, messages.length);
  assert.equal(result.dropped, 0);
});

// --- the breakdown ----------------------------------------------------------
//
// The ring says how full; this says full of what, which is the question
// anybody who sees it near the top immediately has.

test("each kind of message lands in its own category", () => {
  const rows = breakdown([
    { role: "system", content: "environment metadata" },
    { role: "user", content: "do the thing" },
    { role: "assistant", content: "reading it" },
    { role: "tool", tool_call_id: "c1", content: "x".repeat(400) },
  ]);

  const names = rows.map((row) => row.name);
  assert.ok(names.includes("Environment"));
  assert.ok(names.includes("Your messages"));
  assert.ok(names.includes("Assistant"));
  assert.ok(names.includes("Tool results"));
});

test("attached files are their own row, not part of the environment", () => {
  /* They are the row somebody can act on - unpin a file and it goes. */
  const rows = breakdown([
    { role: "system", content: "environment metadata" },
    { role: "system", content: `${ATTACHMENT_MARKER} to this question:${chr10()}...` },
  ]);

  assert.ok(rows.some((row) => row.name === "Attached files"));
  assert.ok(rows.some((row) => row.name === "Environment"));
});

test("the tool schemas are counted, because they are sent every time", () => {
  /* A handful of MCP servers can put more schema in front of the model than
     the conversation has words. */
  const tools = [{ type: "function", function: { name: "read_file", parameters: {} } }];
  const rows = breakdown([{ role: "user", content: "hi" }], tools);

  const definitions = rows.find((row) => row.name === "Tool definitions");
  assert.ok(definitions);
  assert.ok((definitions?.tokens ?? 0) > 0);
});

test("no tools means no row for them, rather than a zero", () => {
  const rows = breakdown([{ role: "user", content: "hi" }], []);
  assert.ok(!rows.some((row) => row.name === "Tool definitions"));
});

test("the biggest consumer is listed first", () => {
  const rows = breakdown([
    { role: "user", content: "short" },
    { role: "tool", tool_call_id: "c1", content: "x".repeat(8000) },
  ]);

  assert.equal(rows[0].name, "Tool results");
  assert.ok(rows[0].tokens > rows[1].tokens);
});

test("several messages of one kind are summed, not listed twice", () => {
  const rows = breakdown([
    { role: "tool", tool_call_id: "a", content: "x".repeat(400) },
    { role: "tool", tool_call_id: "b", content: "x".repeat(400) },
  ]);

  assert.equal(rows.filter((row) => row.name === "Tool results").length, 1);
  assert.ok(rows[0].tokens >= 200);
});

test("an empty conversation breaks down into nothing", () => {
  assert.deepEqual(breakdown([], []), []);
});

function chr10(): string {
  return String.fromCharCode(10);
}
