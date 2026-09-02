import assert from "node:assert/strict";
import { test } from "node:test";
import { collapse } from "../src/format.ts";

// --- the query ---------------------------------------------------------------

test("a multi-line selection becomes one line", () => {
  assert.equal(collapse("func main() {\n\tstart()\n}"), "func main() { start() }");
});

test("a selection longer than the API accepts is cut to fit", () => {
  assert.equal(collapse("x".repeat(3000)).length, 2000);
});
