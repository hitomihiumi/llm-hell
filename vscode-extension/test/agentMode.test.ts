import assert from "node:assert/strict";
import { test } from "node:test";
import { needsApproval } from "../src/agentMode.ts";

/**
 * What each of the three modes actually stops for.
 *
 * Worth pinning because two of the three used to mean the same thing:
 * `assisted` asked about exactly what `manual` did, and `autonomous` still
 * asked before every terminal command - so the mode chosen precisely to avoid
 * being interrupted interrupted the user on the tool an agent reaches for
 * most. These tests are what keep the three names distinct.
 */

test("manual asks before writing a file and before running a command", () => {
  assert.ok(needsApproval("manual", "write_file"));
  assert.ok(needsApproval("manual", "run_terminal"));
});

test("assisted lets writes through and still stops at the terminal", () => {
  assert.ok(!needsApproval("assisted", "write_file"));
  assert.ok(needsApproval("assisted", "run_terminal"));
});

test("autonomous asks about nothing at all", () => {
  /* Including run_terminal. That is the point of the mode, and it is not
     unguarded - executeTool refuses a path outside the workspace before this
     is ever consulted. */
  assert.ok(!needsApproval("autonomous", "write_file"));
  assert.ok(!needsApproval("autonomous", "run_terminal"));
});

test("the read-only tools are never asked about, in any mode", () => {
  for (const mode of ["manual", "assisted", "autonomous"] as const) {
    for (const tool of ["read_file", "list_directory", "search_knowledge_base"]) {
      assert.ok(!needsApproval(mode, tool), `${mode} should not ask about ${tool}`);
    }
  }
});

test("the three modes are genuinely different from each other", () => {
  const shape = (mode: "manual" | "assisted" | "autonomous") =>
    ["write_file", "run_terminal"].filter((tool) => needsApproval(mode, tool)).join(",");

  assert.equal(new Set([shape("manual"), shape("assisted"), shape("autonomous")]).size, 3);
});
