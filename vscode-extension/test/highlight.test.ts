import assert from "node:assert/strict";
import { test } from "node:test";
import { highlight, languageFromPath } from "../src/highlight.ts";

/**
 * The colours on a diff line.
 *
 * One property matters more than any of the others and is checked from
 * several directions: nothing reaches the page unescaped. This runs on file
 * content the model has just written, which is the least trustworthy text in
 * the transcript, and every branch of the tokeniser has its own chance to get
 * it wrong.
 */

test("keywords, strings, numbers and comments each get their own class", () => {
  const html = highlight('const x = "hi"; // note', "ts");

  assert.ok(html.includes('class="hl-keyword">const<'));
  assert.ok(html.includes("hl-string"));
  assert.ok(html.includes("hl-comment"));
});

test("a number is coloured, including hex and exponents", () => {
  assert.ok(highlight("const a = 42;", "ts").includes("hl-number"));
  assert.ok(highlight("0xFF", "ts").includes("hl-number"));
  assert.ok(highlight("1.5e-3", "ts").includes("hl-number"));
});

test("a name followed by a paren reads as a call", () => {
  const html = highlight("doThing(1)", "ts");

  assert.ok(html.includes('class="hl-call">doThing<'));
});

test("a keyword inside a string stays inside the string", () => {
  /* Strings are matched before words, so `return` here must not be painted as
     a keyword sitting in the middle of a string span. */
  const html = highlight('const s = "return false";', "ts");

  const stringSpan = html.slice(html.indexOf("hl-string"));
  assert.ok(!stringSpan.includes("hl-keyword"));
});

test("a comment marker inside a string does not start a comment", () => {
  const html = highlight('const url = "https://example.com";', "ts");

  assert.ok(!html.includes("hl-comment"));
});

test("hash starts a comment in Python but not in TypeScript", () => {
  /* In TypeScript a `#` is a private field, and painting the rest of the line
     as a comment would be worse than leaving it plain. */
  assert.ok(highlight("# a note", "py").includes("hl-comment"));
  assert.ok(!highlight("this.#count = 1;", "ts").includes("hl-comment"));
});

test("double dash starts a comment in SQL but not in TypeScript", () => {
  assert.ok(highlight("-- a note", "sql").includes("hl-comment"));
  assert.ok(!highlight("count--;", "ts").includes("hl-comment"));
});

// --- escaping ------------------------------------------------------------------

test("markup in plain code cannot reach the page", () => {
  const html = highlight('<script>alert("x")</script>', "html");

  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("markup inside a string is escaped as well as coloured", () => {
  const html = highlight('const t = "<img onerror=evil>";', "ts");

  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

test("markup inside a comment is escaped", () => {
  const html = highlight("// <img onerror=evil>", "ts");

  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

test("an unterminated string does not swallow the rest of the line", () => {
  /* A diff shows one line at a time, cut out of its file, so half-open
     strings are normal rather than exceptional. */
  const html = highlight('const s = "unterminated', "ts");

  assert.ok(html.includes("unterminated"));
  assert.ok(html.includes("hl-keyword"));
});

test("the visible text survives highlighting unchanged", () => {
  /* Whatever the colouring does, it must not lose or reorder a character -
     this is a diff, and a dropped character is a lie about the file. */
  for (const code of [
    'const x = "hi"; // note',
    "def f(a, b):  # add",
    "SELECT * FROM t -- all",
    "a && b || c",
    "  indented(1);",
    "",
  ]) {
    const text = highlight(code, "ts")
      .replace(/<[^>]*>/g, "")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&amp;/g, "&");
    assert.equal(text, code);
  }
});

// --- language from a path --------------------------------------------------------

test("the language comes from the file extension", () => {
  assert.equal(languageFromPath("src/sorting.ts"), "ts");
  assert.equal(languageFromPath("C:\repomain.py"), "py");
});

test("an extensionless file falls back to its own name", () => {
  /* Which is what makes `Dockerfile` and `Makefile` highlight at all. */
  assert.equal(languageFromPath("Dockerfile"), "dockerfile");
});

test("no path means no language, not a crash", () => {
  assert.equal(languageFromPath(undefined), "");
});
