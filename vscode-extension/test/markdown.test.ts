import assert from "node:assert/strict";
import { test } from "node:test";
import {
  isShellLanguage,
  linkCitationsForWebview,
  renderAnswer,
  renderMarkdown,
} from "../src/markdown.ts";

/**
 * The webview's own renderer, not a library's.
 *
 * The one property every test here is really checking is the same one:
 * nothing a model writes can become a tag. An answer is model output, and a
 * document full of prompt injection or an accidental `<img onerror=...>` in
 * a transcribed page is exactly the kind of text this has to survive without
 * turning into markup.
 */

// --- escaping is the point ------------------------------------------------------

test("a literal angle bracket never becomes a tag", () => {
  const html = renderMarkdown("if x < y and y > z, do the thing");

  assert.ok(!html.includes("<y"));
  assert.ok(html.includes("&lt;") || html.includes("&gt;"));
});

test("a script tag in the source stays text", () => {
  const html = renderMarkdown("click here <script>alert(1)</script>");

  assert.ok(!/<script>/i.test(html));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("an attribute-injection attempt inside a link URL is inert", () => {
  /* The URL comes straight from the model / the backend's citations, and
     both are outside this extension's control. */
  const html = renderMarkdown('[click](https://example.com/"onmouseover="alert(1))');

  assert.ok(!html.includes('"onmouseover='));
});

test("markup inside a code span is shown, not executed", () => {
  const html = renderMarkdown("Run `<b>not bold</b>` literally.");

  assert.ok(html.includes("<code>&lt;b&gt;not bold&lt;/b&gt;</code>"));
});

test("markup inside a fenced code block is shown, not executed", () => {
  const html = renderMarkdown("```html\n<script>evil()</script>\n```");

  assert.ok(html.includes("&lt;script&gt;evil()&lt;/script&gt;"));
  assert.ok(!/<script>evil/i.test(html));
});

// --- the constructs these answers actually use ----------------------------------

test("a paragraph is a paragraph", () => {
  assert.equal(renderMarkdown("Just a sentence."), "<p>Just a sentence.</p>");
});

test("two paragraphs stay apart", () => {
  const html = renderMarkdown("First.\n\nSecond.");

  assert.equal(html, "<p>First.</p>\n<p>Second.</p>");
});

test("a run of lines with no blank line is one paragraph", () => {
  /* Streamed text arrives a token at a time and line-wraps arbitrarily; a
     paragraph must survive that without fracturing into one <p> per line. */
  const html = renderMarkdown("The gyro is an\nMPU6000, on the\nunderside of the board.");

  assert.equal(html.match(/<p>/g)?.length, 1);
  assert.ok(html.includes("The gyro is an MPU6000, on the underside of the board."));
});

test("bold survives", () => {
  assert.ok(
    renderMarkdown("The **USB port** is on the left.").includes("<strong>USB port</strong>"),
  );
});

test("a fenced code block keeps its language and its indentation", () => {
  const html = renderMarkdown("```python\ndef f():\n    return 1\n```");

  assert.ok(html.includes('<code class="language-python">'));
  assert.ok(html.includes("    return 1"));
});

test("a bullet list is one list, not one paragraph per line", () => {
  const html = renderMarkdown("- first\n- second\n- third");

  assert.equal(html.match(/<ul>/g)?.length, 1);
  assert.equal(html.match(/<li>/g)?.length, 3);
});

test("a heading is a heading", () => {
  assert.ok(renderMarkdown("## Sources").includes("<h2>Sources</h2>"));
});

test("a plain link renders", () => {
  const html = renderMarkdown("See [the README](https://example.com/README.md).");

  assert.ok(html.includes('<a href="https://example.com/README.md">the README</a>'));
});

// --- citations -------------------------------------------------------------------

test("a citation becomes a link to the result it names", () => {
  const linked = linkCitationsForWebview("The gyro is an MPU6000 [1].", [
    { n: 1, url: "https://gitlab.example.com/board/-/blob/main/schematic.md" },
  ]);

  assert.ok(linked.includes("[[1]](https://gitlab.example.com/board/-/blob/main/schematic.md)"));
});

test("a citation with no url is left as plain text", () => {
  const linked = linkCitationsForWebview("See [2].", [{ n: 2, url: null }]);

  assert.equal(linked, "See [2].");
});

test("renderAnswer links and renders in one call", () => {
  const html = renderAnswer("The gyro is an MPU6000 [1].", [
    { n: 1, url: "https://example.com/x" },
  ]);

  assert.ok(html.includes('<a href="https://example.com/x">[1]</a>'));
});

test("renderAnswer still escapes what the model wrote around a citation", () => {
  const html = renderAnswer("<b>bold claim</b> [1].", [{ n: 1, url: "https://example.com/x" }]);

  assert.ok(!/<b>bold claim<\/b>/.test(html));
  assert.ok(html.includes("&lt;b&gt;"));
});

// --- code block actions ------------------------------------------------------------

test("a fenced code block carries a toolbar and names its language", () => {
  const html = renderMarkdown("```ts\nconst x = 1;\n```");

  assert.ok(html.includes('class="code-block"'));
  assert.ok(html.includes('data-language="ts"'));
  assert.ok(html.includes('data-code-action="copy"'));
  assert.ok(html.includes('data-code-action="insert"'));
  assert.ok(html.includes('data-code-action="new-file"'));
  assert.ok(html.includes("const x = 1;"));
});

test("a fence with no language still renders, labelled text", () => {
  const html = renderMarkdown("```\nplain\n```");

  assert.ok(html.includes(">text<"));
  assert.ok(html.includes('data-language=""'));
});

test("run-in-terminal is offered on a shell fence and withheld from a source one", () => {
  /* The action only types the command into a terminal, never runs it - but
     offering "run this" against a TypeScript file is nonsense regardless. */
  assert.ok(renderMarkdown("```bash\nls -la\n```").includes('data-code-action="terminal"'));
  assert.ok(!renderMarkdown("```python\nprint(1)\n```").includes('data-code-action="terminal"'));
});

test("isShellLanguage covers the shells people actually write, and nothing else", () => {
  for (const language of [
    "",
    "bash",
    "sh",
    "zsh",
    "shell",
    "console",
    "powershell",
    "ps1",
    "cmd",
  ]) {
    assert.ok(isShellLanguage(language), `${language || "(none)"} should be shell`);
  }
  for (const language of ["ts", "python", "json", "yaml", "go"]) {
    assert.ok(!isShellLanguage(language), `${language} should not be shell`);
  }
});

test("isShellLanguage ignores case and surrounding space", () => {
  assert.ok(isShellLanguage("  Bash "));
  assert.ok(isShellLanguage("PowerShell"));
});

test("the toolbar cannot be escaped through a language tag", () => {
  /* The language reaches an HTML attribute, so a fence claiming to be
     `ts" onload="evil()` must not be able to close it. */
  const html = renderMarkdown('```ts" onload="evil()\ncode\n```');

  assert.ok(!html.includes('onload="evil()'));
});

test("code inside a block is still escaped now that it sits in a wrapper", () => {
  const html = renderMarkdown("```html\n<script>alert(1)</script>\n```");

  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(!html.includes("<script>alert(1)</script>"));
});

// --- tables ------------------------------------------------------------------
//
// Answers arrive full of them - a requirements matrix, a comparison, a
// per-source breakdown - and without this they were rendered as one long
// paragraph of pipes, because a paragraph joins its lines with a space.

test("a table becomes a table", () => {
  const html = renderMarkdown(
    ["| Requirement | Minimum |", "|---|---|", "| Node.js | 20.9.0 |"].join("\n"),
  );

  assert.ok(html.includes("<table"));
  assert.ok(html.includes("<th>Requirement</th>"));
  assert.ok(html.includes("<td>20.9.0</td>"));
  assert.ok(!html.includes("|---|"), "the delimiter row is structure, not content");
});

test("a delimiter row with spaces and colons is still a delimiter row", () => {
  const html = renderMarkdown(["| a | b | c |", "| :-- | :-: | --: |", "| 1 | 2 | 3 |"].join("\n"));

  assert.ok(html.includes('<th style="text-align:left">a</th>'));
  assert.ok(html.includes('<th style="text-align:center">b</th>'));
  assert.ok(html.includes('<th style="text-align:right">c</th>'));
});

test("prose that merely contains a pipe is left as prose", () => {
  /* The whole reason the header is only a header when a delimiter row
     follows it: `a | b` in a sentence is not a table. */
  const html = renderMarkdown("use a | b in a sentence\nand a second line");

  assert.ok(!html.includes("<table"));
  assert.ok(html.includes("<p>"));
});

test("a short row is padded so the columns stay under their headings", () => {
  const html = renderMarkdown(["| a | b | c |", "|---|---|---|", "| 1 |"].join("\n"));

  assert.equal(html.match(/<td/g)?.length, 3);
});

test("a row longer than the header is cut rather than skewing the table", () => {
  const html = renderMarkdown(["| a | b |", "|---|---|", "| 1 | 2 | 3 |"].join("\n"));

  assert.equal(html.match(/<td/g)?.length, 2);
});

test("an escaped pipe is content, not a cell boundary", () => {
  const html = renderMarkdown(["| pattern |", "|---|", "| a \\| b |"].join("\n"));

  assert.equal(html.match(/<td/g)?.length, 1);
  assert.ok(html.includes("a | b"));
});

test("a table ends where its rows do", () => {
  const html = renderMarkdown(["| a |", "|---|", "| 1 |", "", "a following paragraph"].join("\n"));

  assert.ok(html.includes("</table>"));
  assert.ok(html.includes("<p>a following paragraph</p>"));
});

test("cell content is inline-rendered and still escaped", () => {
  const html = renderMarkdown(["| what |", "|---|", "| **bold** and <script> |"].join("\n"));

  assert.ok(html.includes("<strong>bold</strong>"));
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(!html.includes("<script>"));
});

test("a table without a delimiter row is not a table", () => {
  const html = renderMarkdown(["| a | b |", "| 1 | 2 |"].join("\n"));

  assert.ok(!html.includes("<table"));
});
