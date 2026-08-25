import assert from "node:assert/strict";
import { test } from "node:test";
import { diffLines } from "../src/diff.ts";

/**
 * What a `write_file` changed.
 *
 * "Wrote 1240 bytes to sorting.ts" is the one summary of a write that cannot
 * be checked, which is the whole reason this exists. The properties worth
 * pinning are that it is correct on the small cases, that it stays bounded on
 * the large ones, and that it never claims a change that did not happen.
 */

const added = (result: { lines: { kind: string; text: string }[] }) =>
  result.lines.filter((line) => line.kind === "add").map((line) => line.text);
const removed = (result: { lines: { kind: string; text: string }[] }) =>
  result.lines.filter((line) => line.kind === "remove").map((line) => line.text);

test("a new file is all additions", () => {
  const diff = diffLines("", "one\ntwo");

  assert.deepEqual(added(diff), ["one", "two"]);
  assert.equal(diff.removed, 0);
});

test("an unchanged write reports no change at all", () => {
  /* A model rewriting a file byte-identically is common, and showing a diff
     of nothing would be worse than showing nothing. */
  const diff = diffLines("same\ncontent", "same\ncontent");

  assert.deepEqual(diff.lines, []);
  assert.equal(diff.added, 0);
  assert.equal(diff.removed, 0);
});

test("a one-line change shows that line, not the whole file", () => {
  const before = ["a", "b", "c", "d", "e"].join("\n");
  const after = ["a", "b", "CHANGED", "d", "e"].join("\n");

  const diff = diffLines(before, after);

  assert.deepEqual(added(diff), ["CHANGED"]);
  assert.deepEqual(removed(diff), ["c"]);
  assert.equal(diff.added, 1);
  assert.equal(diff.removed, 1);
});

test("an inserted line is an addition with nothing removed", () => {
  const diff = diffLines("a\nc", "a\nb\nc");

  assert.deepEqual(added(diff), ["b"]);
  assert.equal(diff.removed, 0);
});

test("a deleted line is a removal with nothing added", () => {
  const diff = diffLines("a\nb\nc", "a\nc");

  assert.deepEqual(removed(diff), ["b"]);
  assert.equal(diff.added, 0);
});

test("a trailing newline is a terminator, not a phantom empty line", () => {
  /* Otherwise every file that ends properly reports a change it did not
     make. */
  const diff = diffLines("a\nb\n", "a\nb");

  assert.deepEqual(diff.lines, []);
});

test("unchanged runs are collapsed into a gap rather than printed", () => {
  /* Changing one line in a 900-line file must not render 900 lines to say
     so. */
  const before = Array.from({ length: 400 }, (_, i) => `line ${i}`).join("\n");
  const after = before.replace("line 200", "line 200 CHANGED");

  const diff = diffLines(before, after);
  const gaps = diff.lines.filter((line) => line.kind === "gap");

  assert.ok(gaps.length >= 1, "long unchanged runs become gaps");
  assert.ok(diff.lines.length < 40, `rendered ${diff.lines.length} lines, expected a handful`);
});

test("a change keeps a little context around it", () => {
  const before = Array.from({ length: 50 }, (_, i) => `line ${i}`).join("\n");
  const after = before.replace("line 25", "CHANGED");

  const diff = diffLines(before, after);

  assert.ok(diff.lines.some((line) => line.kind === "context" && line.text === "line 24"));
  assert.ok(diff.lines.some((line) => line.kind === "context" && line.text === "line 26"));
});

test("a rewrite too large to compare is summarised rather than attempted", () => {
  /* The LCS table is quadratic. This is a guard against locking the extension
     host, not a display limit - everything it does compare, it shows. */
  const before = Array.from({ length: 12000 }, (_, i) => `old ${i}`).join("\n");
  const after = Array.from({ length: 12000 }, (_, i) => `new ${i}`).join("\n");

  const diff = diffLines(before, after);

  assert.equal(diff.truncated, true);
  assert.equal(diff.lines.length, 1);
  assert.equal(diff.lines[0].kind, "gap");
});

test("a big file with a small change is still compared properly", () => {
  /* The prefix and suffix are stripped before the expensive part, so file
     size alone does not trigger the give-up path. */
  const lines = Array.from({ length: 4000 }, (_, i) => `line ${i}`);
  const before = lines.join("\n");
  lines[2000] = "CHANGED";
  const after = lines.join("\n");

  const diff = diffLines(before, after);

  assert.equal(diff.truncated, false);
  assert.deepEqual(added(diff), ["CHANGED"]);
});

test("every changed line is rendered, however many there are", () => {
  /* The diff is not shortened. A large rewrite is read by scrolling the pane,
     not by the renderer deciding on the reader's behalf where to stop. */
  const before = Array.from({ length: 1000 }, (_, i) => `line ${i}`).join("\n");
  const after = Array.from({ length: 1000 }, (_, i) => `line ${i} edited`).join("\n");

  const diff = diffLines(before, after);
  const changed = diff.lines.filter((line) => line.kind === "add" || line.kind === "remove");

  assert.equal(changed.length, 2000);
  assert.equal(diff.truncated, false);
});

test("counts describe the whole change, not just the part shown", () => {
  /* Counts are over the whole comparison, including the unchanged runs that
     collapse into gaps. */
  const before = Array.from({ length: 300 }, (_, i) => `old ${i}`).join("\n");
  const after = Array.from({ length: 300 }, (_, i) => `new ${i}`).join("\n");

  const diff = diffLines(before, after);

  assert.equal(diff.added, 300);
  assert.equal(diff.removed, 300);
});

test("windows line endings do not read as a change on every line", () => {
  const diff = diffLines("a\r\nb\r\nc", "a\nb\nc");

  assert.deepEqual(diff.lines, []);
});
