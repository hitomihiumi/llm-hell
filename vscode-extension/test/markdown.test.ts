import assert from "node:assert/strict";
import { test } from "node:test";
import { linkCitationsForWebview, renderAnswer, renderMarkdown } from "../src/markdown.ts";

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
