import assert from "node:assert/strict";
import { test } from "node:test";
import type { MessagePart } from "../src/webviewProtocol.ts";
import {
  appendPart,
  fullToolArgs,
  summariseToolArgs,
  visibleText,
} from "../src/webviewProtocol.ts";

/**
 * What a tool-call card's subtitle says, before the call has even finished.
 *
 * This is read while a run_terminal call the user has not yet confirmed is
 * still sitting there - it is the ONE piece of information standing between
 * "approve" and "approve blind". Wrong here is worse than wrong most places.
 */

test("run_terminal shows the actual command", () => {
  assert.equal(
    summariseToolArgs("run_terminal", '{"command": "rm -rf node_modules"}'),
    "rm -rf node_modules",
  );
});

test("read_file shows the path", () => {
  assert.equal(summariseToolArgs("read_file", '{"path": "src/index.ts"}'), "src/index.ts");
});

test("search_knowledge_base shows the query", () => {
  assert.equal(
    summariseToolArgs("search_knowledge_base", '{"query": "auth-service README"}'),
    "auth-service README",
  );
});

test("read_knowledge_base_result shows the id", () => {
  assert.equal(
    summariseToolArgs("read_knowledge_base_result", '{"id": "gitlab:code:3"}'),
    "gitlab:code:3",
  );
});

test("an unrecognised tool falls back to key=value pairs", () => {
  assert.equal(summariseToolArgs("some_future_tool", '{"a": 1, "b": "x"}'), "a=1, b=x");
});

test("arguments that are not JSON still produce something, not a crash", () => {
  /* Streamed tool_call arguments can arrive as a fragment mid-call - this
     runs while the card is still updating, not just once it is complete. */
  assert.equal(summariseToolArgs("run_terminal", '{"command": "ec'), '{"command": "ec');
});

test("a long command is cut rather than blowing out the card", () => {
  const summary = summariseToolArgs("run_terminal", JSON.stringify({ command: "x".repeat(300) }));

  assert.ok(summary.length < 110);
  assert.ok(summary.endsWith("…"));
});

test("whitespace in a multi-line command collapses to one line", () => {
  /* The card is one line; a command with embedded newlines must not break
     the layout of the row it sits in. */
  const summary = summariseToolArgs(
    "run_terminal",
    JSON.stringify({ command: "echo a\n\necho b" }),
  );

  assert.equal(summary, "echo a echo b");
});

// --- the argument shown on an approval card ------------------------------------

test("fullToolArgs keeps a multi-line command whole", () => {
  /* This is the string a person reads before allowing a command to run, so
     the one-line summary's flattening and 100-character cut are exactly
     wrong here. */
  const command = "cd build\ncmake ..\nmake -j8 && ./run --with a very long list of arguments here";
  const full = fullToolArgs("run_terminal", JSON.stringify({ command }));

  assert.equal(full, command);
});

test("fullToolArgs falls back to the summary for tools that never ask", () => {
  assert.equal(
    fullToolArgs("search_knowledge_base", JSON.stringify({ query: "auth-service" })),
    "auth-service",
  );
});

test("fullToolArgs hands back unparseable arguments rather than swallowing them", () => {
  assert.equal(fullToolArgs("run_terminal", "{not json"), "{not json");
});

// --- the parts sequence ----------------------------------------------------------

test("visibleText is the answer, without the model's thinking", () => {
  /* Reasoning is the model talking to itself. Feeding it back as history
     invites the next turn to answer the thinking rather than the question,
     and copying it hands the user something they did not ask for. */
  const text = visibleText({
    id: "m1",
    role: "assistant",
    status: "done",
    parts: [
      { kind: "reasoning", text: "Let me think about this." },
      { kind: "text", text: "The version is 2.0.0." },
      { kind: "tool", call: { id: "c1", name: "read_file", argsSummary: "a", status: "done" } },
      { kind: "text", text: " Anything else?" },
    ],
  });

  assert.equal(text, "The version is 2.0.0. Anything else?");
});

test("appendPart grows the run in progress instead of fragmenting it", () => {
  /* Streaming arrives a token at a time; a part per token would put a
     paragraph break between every pair of them. */
  const parts: MessagePart[] = [];
  appendPart(parts, "text", "Hello");
  appendPart(parts, "text", " world");

  assert.equal(parts.length, 1);
  assert.deepEqual(parts[0], { kind: "text", text: "Hello world" });
});

test("appendPart starts a new part when the channel changes", () => {
  const parts: MessagePart[] = [];
  appendPart(parts, "reasoning", "thinking");
  appendPart(parts, "text", "answering");
  appendPart(parts, "reasoning", "more thinking");

  assert.deepEqual(
    parts.map((part) => part.kind),
    ["reasoning", "text", "reasoning"],
  );
});

test("appendPart does not merge across a tool call", () => {
  /* Text before and after a tool are separate thoughts, and merging them
     would put the tool card in the wrong place. */
  const parts: MessagePart[] = [{ kind: "text", text: "before" }];
  parts.push({
    kind: "tool",
    call: { id: "c1", name: "read_file", argsSummary: "a", status: "done" },
  });
  appendPart(parts, "text", "after");

  assert.equal(parts.length, 3);
  assert.equal(parts[2].kind, "text");
});
