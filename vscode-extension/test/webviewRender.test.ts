import assert from "node:assert/strict";
import { test } from "node:test";
import { renderMessage } from "../src/webview/render.ts";
import type { ChatMessageView } from "../src/webviewProtocol.ts";

/**
 * What one transcript entry becomes in the panel, as a string - no DOM, no
 * webview, no browser. That is what makes checking it cheap enough to do for
 * every state a message passes through: streaming with nothing yet, a
 * finished answer, a failed tool call, a 404'd reference.
 */

function message(overrides: Partial<ChatMessageView> = {}): ChatMessageView {
  return {
    id: "m1",
    role: "assistant",
    mode: "kb",
    text: "",
    status: "done",
    ...overrides,
  };
}

test("a user message shows their words as plain text, not as Markdown", () => {
  /* The user's own question is not something to run bold/italic/link
     detection over - it is quoted back, not rendered. */
  const html = renderMessage(message({ role: "user", text: "what about **this**?" }));

  assert.ok(html.includes("what about **this**?"));
  assert.ok(!html.includes("<strong>"));
});

test("an assistant message renders as Markdown", () => {
  const html = renderMessage(message({ text: "The **USB port** is on the left." }));

  assert.ok(html.includes("<strong>USB port</strong>"));
});

test("a message streaming with nothing yet shows a working indicator, not an empty box", () => {
  const html = renderMessage(message({ status: "streaming", text: "" }));

  assert.ok(html.includes("message-pending"));
});

test("a message streaming with tool calls already running does not also show the empty-box indicator", () => {
  const html = renderMessage(
    message({
      status: "streaming",
      text: "",
      toolCalls: [{ id: "c1", name: "read_file", argsSummary: "a.ts", status: "running" }],
    }),
  );

  assert.ok(!html.includes("message-pending"));
  assert.ok(html.includes("read_file"));
});

test("a tool call card shows its name, arguments and status", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        { id: "c1", name: "run_terminal", argsSummary: "rm -rf node_modules", status: "running" },
      ],
    }),
  );

  assert.ok(html.includes("run_terminal"));
  assert.ok(html.includes("rm -rf node_modules"));
  assert.ok(html.includes("Running"));
});

test("a finished tool call's result is shown, escaped", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "run_terminal",
          argsSummary: "echo <hi>",
          status: "done",
          result: "<hi> printed",
        },
      ],
    }),
  );

  assert.ok(html.includes("&lt;hi&gt; printed"));
  assert.ok(!html.includes("<hi> printed"));
});

test("references become clickable chips carrying their id and url", () => {
  const html = renderMessage(
    message({
      references: [
        {
          hitId: "gitlab:code:3",
          title: "main.go",
          source: "gitlab",
          url: "https://x.test/main.go",
        },
      ],
    }),
  );

  assert.ok(html.includes('data-open-reference="gitlab:code:3"'));
  assert.ok(html.includes('data-url="https://x.test/main.go"'));
});

test("a reference with no url still gets a chip, with an empty url attribute", () => {
  /* The click handler falls back to opening it through the client instead -
     that decision lives in main.ts, but the chip itself must not vanish. */
  const html = renderMessage(
    message({
      references: [
        { hitId: "postgres_kb:articles:4", title: "Runbook", source: "postgres_kb", url: null },
      ],
    }),
  );

  assert.ok(html.includes('data-open-reference="postgres_kb:articles:4"'));
  assert.ok(html.includes('data-url=""'));
});

test("citations are numbered and linked", () => {
  const html = renderMessage(
    message({
      citations: [{ n: 1, title: "README.md", url: "https://x.test/README.md", source: "gitlab" }],
    }),
  );

  assert.ok(html.includes(">1. <"));
  assert.ok(html.includes('href="https://x.test/README.md"'));
});

test("the citations list is a <ul>, not an <ol>", () => {
  /* An <ol> numbers its own items, which collided with the citation's own
     number and rendered "1. 1." for the first entry - and citation.n is not
     always the same as an item's position anyway, since the API already
     drops the numbers a model invented. */
  const html = renderMessage(
    message({ citations: [{ n: 1, title: "README.md", url: null, source: "gitlab" }] }),
  );

  assert.ok(html.includes('<ul class="citations">'));
  assert.ok(!html.includes("<ol"));
});

test("an error is shown, escaped, without also showing an empty answer", () => {
  const html = renderMessage(message({ status: "error", text: "", error: "<no auth>" }));

  assert.ok(html.includes("&lt;no auth&gt;"));
  assert.ok(!html.includes("message-pending"));
});

test("a title with an embedded quote cannot break out of the chip's attribute", () => {
  const html = renderMessage(
    message({
      references: [{ hitId: "x", title: 'a" onclick="evil()', source: "gitlab", url: null }],
    }),
  );

  assert.ok(!html.includes('onclick="evil()'));
});
