import assert from "node:assert/strict";
import { test } from "node:test";
import {
  looksTruncated,
  renderContextItems,
  renderMessage,
  renderUsageRing,
  renderWelcome,
} from "../src/webview/render.ts";
import type { ChatMessageView, MessagePart, ToolCallView } from "../src/webviewProtocol.ts";

/**
 * What one transcript entry becomes in the panel, as a string - no DOM, no
 * webview, no browser. That is what makes checking it cheap enough to do for
 * every state a message passes through: streaming with nothing yet, a
 * finished answer, a failed tool call, a 404'd reference.
 */

/**
 * A message, built from the shorthand the tests below actually care about.
 *
 * A real message is a `parts` sequence - text, reasoning and tool calls in
 * the order they arrived - but most tests are about one of those at a time,
 * so `text` and `toolCalls` are accepted here and assembled into parts. Tests
 * that are specifically about ordering pass `parts` directly.
 */
function message(
  overrides: Partial<ChatMessageView> & { text?: string; toolCalls?: ToolCallView[] } = {},
): ChatMessageView {
  const { text, toolCalls, parts, ...rest } = overrides;
  const built: MessagePart[] = parts ?? [
    ...(text ? [{ kind: "text" as const, text }] : []),
    ...(toolCalls ?? []).map((call) => ({ kind: "tool" as const, call })),
  ];
  return {
    id: "m1",
    role: "assistant",
    parts: built,
    status: "done",
    ...rest,
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

test("an error is shown, escaped, without also showing an empty answer", () => {
  const html = renderMessage(message({ status: "error", text: "", error: "<no auth>" }));

  assert.ok(html.includes("&lt;no auth&gt;"));
  assert.ok(!html.includes("message-pending"));
});

// --- the approval card ---------------------------------------------------------

test("a tool awaiting approval asks in the transcript, carrying its call id", () => {
  /* The whole point of owning this surface: the question appears where the
     work is, not as a modal thrown over the editor. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "call-7",
          name: "run_terminal",
          argsSummary: "rm -rf build",
          argsFull: "rm -rf build",
          status: "awaiting",
        },
      ],
    }),
  );

  assert.ok(html.includes('data-confirm-tool="call-7"'));
  assert.ok(html.includes('data-allow="true"'));
  assert.ok(html.includes('data-allow="false"'));
  assert.ok(html.includes('data-always="true"'));
  assert.ok(html.includes("Needs approval"));
});

test("an approval card shows the whole argument, not the one-line summary", () => {
  /* summariseToolArgs flattens newlines and cuts at 100 characters, which is
     right for a collapsed card and wrong for the one moment it matters what
     the command actually says. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "run_terminal",
          argsSummary: "cd build && cmake ..",
          argsFull: "cd build\ncmake ..\nmake -j8",
          status: "awaiting",
        },
      ],
    }),
  );

  assert.ok(html.includes("make -j8"));
});

test("an approval card is not a <details>, so the question cannot be folded shut", () => {
  const html = renderMessage(
    message({
      toolCalls: [{ id: "c1", name: "write_file", argsSummary: "a.ts", status: "awaiting" }],
    }),
  );

  assert.ok(!html.includes("<details"));
});

test("only a pending write offers a diff", () => {
  const write = renderMessage(
    message({
      toolCalls: [{ id: "c1", name: "write_file", argsSummary: "a.ts", status: "awaiting" }],
    }),
  );
  const command = renderMessage(
    message({
      toolCalls: [{ id: "c2", name: "run_terminal", argsSummary: "ls", status: "awaiting" }],
    }),
  );

  assert.ok(write.includes('data-show-diff="c1"'));
  assert.ok(!command.includes("data-show-diff"));
});

test("a command awaiting approval cannot break out of the card through its own text", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "run_terminal",
          argsSummary: "x",
          argsFull: '</pre><img src=x onerror="evil()">',
          status: "awaiting",
        },
      ],
    }),
  );

  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

// --- welcome, actions, context -------------------------------------------------

test("the welcome state offers something to click rather than an empty box", () => {
  const html = renderWelcome();

  assert.ok(html.includes("data-suggestion="));
  assert.ok(html.includes("Coder"));
});

test("a finished answer offers copy and retry; one still streaming does not", () => {
  /* Retrying a half-written answer would race the stream that is writing it. */
  const done = renderMessage(message({ text: "done", status: "done" }));
  const streaming = renderMessage(message({ text: "part", status: "streaming" }));

  assert.ok(done.includes('data-message-action="copy"'));
  assert.ok(done.includes('data-message-action="retry"'));
  assert.ok(!streaming.includes("data-message-action"));
});

test("the user's own message gets no retry button", () => {
  const html = renderMessage(message({ role: "user", text: "hi", status: "done" }));

  assert.ok(!html.includes("data-message-action"));
});

test("only a pinned context item can be removed", () => {
  /* The active file and the selection are what the editor is doing; they go
     into the prompt whether or not anybody asked, so this panel has no
     business offering to take them away. */
  const html = renderContextItems([
    { id: "file:a", label: "a.ts", kind: "file" },
    { id: "selection", label: "selection · 3 lines", kind: "selection" },
    { id: "pinned:b", label: "b.ts", kind: "pinned" },
  ]);

  assert.ok(html.includes('data-remove-context="pinned:b"'));
  assert.ok(!html.includes('data-remove-context="file:a"'));
  assert.ok(!html.includes('data-remove-context="selection"'));
});

test("no context means no chips at all, not an empty row", () => {
  assert.equal(renderContextItems([]), "");
});

test("a filename cannot break out of a context chip's title attribute", () => {
  const html = renderContextItems([
    { id: "pinned:x", label: 'a" onmouseover="evil()', kind: "pinned" },
  ]);

  assert.ok(!html.includes('onmouseover="evil()'));
});

// --- ordering, reasoning ---------------------------------------------------------

test("a tool call renders where it happened, between the prose around it", () => {
  /* The whole reason a message is a parts sequence: an agent turn is prose,
     then a tool, then prose written in the light of what came back. Gathering
     the calls into a list at the bottom loses when each one ran. */
  const html = renderMessage(
    message({
      parts: [
        { kind: "text", text: "Спершу подивлюсь файл." },
        {
          kind: "tool",
          call: { id: "c1", name: "read_file", argsSummary: "a.ts", status: "done", result: "ok" },
        },
        { kind: "text", text: "Версія там 2.0.0." },
      ],
    }),
  );

  const before = html.indexOf("Спершу");
  const tool = html.indexOf("read_file");
  const after = html.indexOf("2.0.0");

  assert.ok(before >= 0 && tool >= 0 && after >= 0);
  assert.ok(before < tool, "the first sentence comes before the tool");
  assert.ok(tool < after, "the tool comes before the sentence it produced");
});

test("reasoning is a folded block, not prose in the answer", () => {
  /* `delta.reasoning` is a separate channel from `delta.content` and reads
     like one. Rendered as ordinary text it was indistinguishable from the
     answer, which is what made the transcript confusing. */
  const html = renderMessage(
    message({
      parts: [
        { kind: "reasoning", text: "The user asks to read package.json. Let me read it." },
        { kind: "text", text: "Версія 2.0.0." },
      ],
    }),
  );

  assert.ok(html.includes('<details class="reasoning"'));
  assert.ok(html.includes("Thinking"));
  assert.ok(html.includes("Let me read it"));
  // and it is not run through the Markdown renderer as if it were the answer
  assert.ok(!html.includes('<div class="message-body">The user asks'));
});

test("reasoning is open while it is the only thing happening and shut once done", () => {
  const streaming = renderMessage(
    message({ status: "streaming", parts: [{ kind: "reasoning", text: "hmm" }] }),
  );
  const settled = renderMessage(
    message({ status: "done", parts: [{ kind: "reasoning", text: "hmm" }] }),
  );

  assert.ok(/<details class="reasoning"[^>]* open>/.test(streaming));
  assert.ok(!/<details class="reasoning"[^>]* open>/.test(settled));
});

test("reasoning cannot smuggle a tag into the page", () => {
  const html = renderMessage(
    message({ parts: [{ kind: "reasoning", text: '<img src=x onerror="evil()">' }] }),
  );

  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

test("a message with nothing in it yet shows the working indicator", () => {
  const html = renderMessage(message({ status: "streaming", parts: [] }));

  assert.ok(html.includes("message-pending"));
});

test("a message already streaming reasoning does not also show the empty indicator", () => {
  const html = renderMessage(
    message({ status: "streaming", parts: [{ kind: "reasoning", text: "thinking…" }] }),
  );

  assert.ok(!html.includes("message-pending"));
});

// --- a sentence the model abandoned --------------------------------------------

test("a sentence abandoned to call a tool is marked, not left looking eaten", () => {
  /* Measured against the live model: it stops emitting content mid-word -
     "що виводить приві" - and switches to tool_calls, and every delta before
     that point does arrive. Rendered plainly it reads as the panel eating the
     end of a word, which is the one thing it is not. */
  const html = renderMessage(
    message({
      parts: [
        { kind: "text", text: "Спершу подивлюсь, що є в про" },
        {
          kind: "tool",
          call: { id: "c1", name: "list_directory", argsSummary: ".", status: "done" },
        },
      ],
    }),
  );

  assert.ok(html.includes("cut-marker"));
  assert.ok(html.includes("…"));
});

test("the marker sits inside the paragraph, not on a line of its own", () => {
  /* renderAnswer returns block-level HTML; appending the span after it would
     drop the ellipsis onto its own line, reading as a new thought rather than
     the end of the truncated one. */
  const html = renderMessage(
    message({
      parts: [
        { kind: "text", text: "Спершу подивлюсь, що є в про" },
        {
          kind: "tool",
          call: { id: "c1", name: "list_directory", argsSummary: ".", status: "done" },
        },
      ],
    }),
  );

  assert.ok(html.includes("…</span></p>"));
});

test("a finished sentence before a tool call is not marked", () => {
  const html = renderMessage(
    message({
      parts: [
        { kind: "text", text: "Дивлюсь, що є в проєкті." },
        {
          kind: "tool",
          call: { id: "c1", name: "list_directory", argsSummary: ".", status: "done" },
        },
      ],
    }),
  );

  assert.ok(!html.includes("cut-marker"));
});

test("text that ends the message is never marked, tool call or not", () => {
  /* The last thing said is not an abandoned sentence - it is the answer, and
     a model is allowed to end without a full stop. */
  const html = renderMessage(message({ parts: [{ kind: "text", text: "Готово, файл створено" }] }));

  assert.ok(!html.includes("cut-marker"));
});

test("a streaming message is not marked while it is still being written", () => {
  /* Half a sentence mid-stream is not abandoned, it is in progress. The
     marker only appears once a tool call has landed after it. */
  const html = renderMessage(
    message({
      status: "streaming",
      parts: [{ kind: "text", text: "Спершу подивлюсь, що є в про" }],
    }),
  );

  assert.ok(!html.includes("cut-marker"));
});

test("the user's own message is never marked", () => {
  const html = renderMessage(
    message({
      role: "user",
      parts: [
        { kind: "text", text: "зроби щось" },
        { kind: "tool", call: { id: "c1", name: "x", argsSummary: "", status: "done" } },
      ],
    }),
  );

  assert.ok(!html.includes("cut-marker"));
});

test("looksTruncated accepts the ways a thought can properly end", () => {
  for (const ending of ["done.", "done!", "done?", "ось так:", "(ось)", "код `x`", "**жирно**"]) {
    assert.ok(!looksTruncated(ending), `${ending} should read as finished`);
  }
  for (const ending of [
    "що виводить приві",
    "у файлі sort.ts, щоб додати",
    "план, а потім запишу код у",
  ]) {
    assert.ok(looksTruncated(ending), `${ending} should read as cut`);
  }
});

test("looksTruncated ignores trailing whitespace", () => {
  assert.ok(!looksTruncated("done.  \n"));
  assert.ok(looksTruncated("cut off  \n"));
});

// --- the diff on a write -------------------------------------------------------

const WRITE_DIFF = {
  lines: [
    { kind: "context" as const, text: "const a = 1;" },
    { kind: "remove" as const, text: "const b = 2;" },
    { kind: "add" as const, text: "const b = 3;" },
    { kind: "gap" as const, text: "40 unchanged lines" },
  ],
  added: 1,
  removed: 1,
  truncated: false,
};

test("a finished write shows what it changed, with a count on the folded card", () => {
  /* "Wrote 1240 bytes" is the one summary of a write that cannot be checked. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "write_file",
          argsSummary: "sorting.ts",
          status: "done",
          result: "Wrote 1240 bytes to sorting.ts.",
          path: "sorting.ts",
          diff: WRITE_DIFF,
        },
      ],
    }),
  );

  assert.ok(html.includes("diff-add"));
  assert.ok(html.includes("diff-remove"));
  // Highlighted, so the line arrives in pieces rather than as one string.
  assert.ok(html.includes("hl-keyword"));
  assert.ok(html.replace(/<[^>]*>/g, "").includes("const b = 3;"));
  assert.ok(html.includes("+1"));
  assert.ok(html.includes("−1"));
});

test("a write awaiting approval shows the diff instead of just the path", () => {
  /* Approving a write without seeing the change is approving a byte count. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "write_file",
          argsSummary: "sorting.ts",
          argsFull: "sorting.ts",
          status: "awaiting",
          path: "sorting.ts",
          diff: WRITE_DIFF,
        },
      ],
    }),
  );

  assert.ok(html.includes("diff-add"));
  assert.ok(html.includes('data-confirm-tool="c1"'));
  assert.ok(!html.includes("tool-approve-args"));
});

test("a tool with no diff still shows its argument", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "run_terminal",
          argsSummary: "pnpm test",
          argsFull: "pnpm test",
          status: "awaiting",
        },
      ],
    }),
  );

  assert.ok(html.includes("tool-approve-args"));
  assert.ok(html.includes("pnpm test"));
});

test("an awaiting tool's argument box carries a scroll key", () => {
  /* Without one, a long `run_terminal` command loses its scroll position
     the moment another token in the same turn triggers a re-render - the
     whole reason `.tool-result` and `.diff-body` carry one already. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "run_terminal",
          argsSummary: "pnpm test",
          argsFull: "pnpm test",
          status: "awaiting",
        },
      ],
    }),
  );

  assert.ok(html.includes('data-scroll-key="args:c1"'));
});

test("read_file's result is syntax-highlighted from its own path", () => {
  /* The result is exactly what an editor tab would show for a.ts - plain
     text was the one tool that gave back real file content and dropped it
     on the floor. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "read_file",
          argsSummary: "a.ts",
          status: "done",
          result: "const x = 1;",
        },
      ],
    }),
  );

  assert.ok(html.includes("hl-keyword"));
  assert.ok(html.replace(/<[^>]*>/g, "").includes("const x = 1;"));
});

test("a read_file error is left plain, not run through the lexer", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "read_file",
          argsSummary: "missing.ts",
          status: "done",
          result: "Error reading file: ENOENT",
        },
      ],
    }),
  );

  assert.ok(html.includes("Error reading file: ENOENT"));
  assert.ok(!html.includes("hl-"));
});

test("other tools' results are not run through the highlighter", () => {
  /* list_directory's output is a listing, not a file's content - it has no
     language to pick, and running it through the lexer anyway would colour
     directory names as though they were code. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "list_directory",
          argsSummary: "src",
          status: "done",
          result: "index.ts (file)\nutils.ts (file)",
        },
      ],
    }),
  );

  assert.ok(!html.includes("hl-"));
});

test("a write that changed nothing says so rather than showing an empty box", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "write_file",
          argsSummary: "a.ts",
          status: "done",
          path: "a.ts",
          diff: { lines: [], added: 0, removed: 0, truncated: false },
        },
      ],
    }),
  );

  assert.ok(html.includes("written unchanged"));
  assert.ok(html.includes("no change"));
});

test("file content in a diff cannot become markup", () => {
  /* This is the least trustworthy text in the transcript: whatever the model
     just decided to write, and `<script>` is an ordinary thing to put in an
     HTML file. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "write_file",
          argsSummary: "index.html",
          status: "done",
          path: "index.html",
          diff: {
            lines: [{ kind: "add", text: '<script>alert("x")</script>' }],
            added: 1,
            removed: 0,
            truncated: false,
          },
        },
      ],
    }),
  );

  assert.ok(!html.includes("<script>alert"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("a path with a quote cannot break out of the diff heading", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "write_file",
          argsSummary: "x",
          status: "done",
          path: 'a" onclick="evil()',
          diff: WRITE_DIFF,
        },
      ],
    }),
  );

  assert.ok(!html.includes('onclick="evil()'));
});

test("a rewrite too large to compare says so", () => {
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "write_file",
          argsSummary: "big.ts",
          status: "done",
          path: "big.ts",
          diff: { ...WRITE_DIFF, truncated: true },
        },
      ],
    }),
  );

  assert.ok(html.includes("too large to compare"));
});

// --- surviving a re-render -----------------------------------------------------

test("a tool card carries a key stable across re-renders", () => {
  /* The transcript is replaced wholesale on every streamed token, and
     `<details>` keeps its open state in the DOM - so without a key to restore
     it against, a card opened mid-answer collapses milliseconds later. */
  const html = renderMessage(
    message({
      toolCalls: [{ id: "call-7", name: "read_file", argsSummary: "a.ts", status: "done" }],
    }),
  );

  assert.ok(html.includes('data-toggle-key="tool:call-7"'));
});

test("a reasoning block carries a key of its own", () => {
  const html = renderMessage(
    message({ id: "m9", parts: [{ kind: "reasoning", text: "thinking" }] }),
  );

  assert.ok(html.includes('data-toggle-key="m9:r0"'));
});

test("two reasoning blocks in one message get different keys", () => {
  /* Keyed on position, so the second block does not inherit the first one's
     open state. */
  const html = renderMessage(
    message({
      parts: [
        { kind: "reasoning", text: "first" },
        { kind: "text", text: "answer" },
        { kind: "reasoning", text: "second" },
      ],
    }),
  );

  assert.ok(html.includes(':r0"'));
  assert.ok(html.includes(':r2"'));
});

test("the scrollable panes inside a card are keyed too", () => {
  /* Scrolling a diff and having it jump back on the next token is the same
     bug wearing a different hat. */
  const html = renderMessage(
    message({
      toolCalls: [
        {
          id: "c1",
          name: "write_file",
          argsSummary: "a.ts",
          status: "done",
          result: "Wrote 10 bytes.",
          path: "a.ts",
          diff: { lines: [{ kind: "add", text: "x" }], added: 1, removed: 0, truncated: false },
        },
      ],
    }),
  );

  assert.ok(html.includes('data-scroll-key="diff:c1"'));
  assert.ok(html.includes('data-scroll-key="result:c1"'));
});

test("a message id with a quote cannot break out of the key attribute", () => {
  const html = renderMessage(
    message({ id: 'm" onclick="evil()', parts: [{ kind: "reasoning", text: "x" }] }),
  );

  assert.ok(!html.includes('onclick="evil()'));
});

// --- the context ring ----------------------------------------------------------

test("the ring carries a tooltip it draws itself, not the browser's", () => {
  /* A native `title` waits a second, is styled by the OS rather than the
     theme, and breaks its lines differently on every platform. */
  const html = renderUsageRing({ used: 8192, budget: 32768 });

  assert.ok(html.includes("<svg"));
  assert.ok(html.includes('class="usage-tip"'));
  assert.ok(!html.includes("title="), "no native tooltip to fight the drawn one");
  assert.match(html, /Context <b>25%<\/b>/);
  assert.match(html, /8,192 of 32,768 tokens/);
});

test("the tooltip says the number is an estimate, because it is", () => {
  assert.match(renderUsageRing({ used: 100, budget: 1000 }), /Estimated, not counted/);
});

test("the tooltip is hidden from a screen reader, which reads the label instead", () => {
  /* Both would say the same thing twice. */
  const html = renderUsageRing({ used: 100, budget: 1000 });

  assert.match(html, /class="usage-tip" role="tooltip" aria-hidden="true"/);
  assert.match(html, /aria-label="Context: 100 of 1,000 tokens, 10 percent full\. Estimated\."/);
});

test("the ring can be reached by keyboard, or the tooltip is mouse-only", () => {
  assert.match(renderUsageRing({ used: 1, budget: 2 }), /class="usage usage-[a-z]+" tabindex="0"/);
});

test("no budget still draws the ring, and says why it is empty", () => {
  /* A gauge that disappears is one nobody trusts. With compaction off there
     is no ceiling to be a fraction of, so the ring shows an empty track and
     the tooltip says so. */
  const html = renderUsageRing({ used: 500, budget: 0 });

  assert.ok(html.includes("<svg"));
  assert.ok(html.includes("usage-off"));
  assert.match(html, /500 tokens, no limit set/);
  assert.match(html, /Compaction is off/);
});

const DASH = /stroke-dasharray="([\d.]+)/;

test("the arc grows with the fraction", () => {
  const quarter = DASH.exec(renderUsageRing({ used: 250, budget: 1000 }));
  const half = DASH.exec(renderUsageRing({ used: 500, budget: 1000 }));

  assert.ok(quarter && half);
  assert.ok(Number(half?.[1]) > Number(quarter?.[1]));
});

test("over budget fills the ring rather than overflowing it", () => {
  const html = renderUsageRing({ used: 90_000, budget: 1_000 });
  const dash = Number(DASH.exec(html)?.[1]);
  const circumference = 2 * Math.PI * 6;

  assert.ok(dash <= circumference + 0.01, `${dash} should not exceed ${circumference}`);
  assert.ok(html.includes("usage-full"));
});

test("the ring is only coloured once it matters", () => {
  assert.ok(renderUsageRing({ used: 100, budget: 1000 }).includes("usage-normal"));
  assert.ok(renderUsageRing({ used: 750, budget: 1000 }).includes("usage-high"));
  assert.ok(renderUsageRing({ used: 950, budget: 1000 }).includes("usage-full"));
});

test("a pass that compacted says what it folded", () => {
  const html = renderUsageRing({
    used: 500,
    budget: 1000,
    compacted: { folded: 3, dropped: 2 },
  });

  assert.match(html, /3 tool result\(s\) folded/);
  assert.match(html, /2 message\(s\) dropped/);
});

test("a quiet ring says nothing about compaction, having nothing to say", () => {
  const html = renderUsageRing({ used: 100, budget: 1000 });

  assert.ok(!html.includes("folded away as this fills"));
  assert.ok(!html.includes("Just compacted"));
});

// --- the breakdown, on click -----------------------------------------------------

test("the ring is a button that says whether its breakdown is open", () => {
  const html = renderUsageRing({ used: 100, budget: 1000 });

  assert.match(html, /role="button"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /data-usage-toggle="true"/);
});

test("each category is a row with its share and its tokens", () => {
  const html = renderUsageRing({
    used: 1000,
    budget: 10_000,
    categories: [
      { name: "Tool results", tokens: 750 },
      { name: "Your messages", tokens: 250 },
    ],
  });

  assert.match(html, /usage-row-name">Tool results/);
  assert.match(html, /width:75%/);
  assert.match(html, /usage-row-tokens">750/);
  assert.match(html, /usage-row-name">Your messages/);
  assert.match(html, /width:25%/);
});

test("the breakdown totals what it lists", () => {
  const html = renderUsageRing({
    used: 1000,
    budget: 10_000,
    categories: [
      { name: "A", tokens: 1200 },
      { name: "B", tokens: 800 },
    ],
  });

  assert.match(html, /2,000 tokens, estimated/);
});

test("nothing sent yet says so, rather than showing an empty box", () => {
  const html = renderUsageRing({ used: 0, budget: 1000, categories: [] });

  assert.match(html, /Nothing sent yet/);
});

test("a category name cannot break out of the panel", () => {
  /* Category names are ours today; the escaping is what keeps that from
     mattering if one ever comes from a tool. */
  const html = renderUsageRing({
    used: 10,
    budget: 100,
    categories: [{ name: '<img src=x onerror="evil()">', tokens: 10 }],
  });

  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});
