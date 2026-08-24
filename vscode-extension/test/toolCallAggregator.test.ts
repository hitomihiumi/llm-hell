import assert from "node:assert/strict";
import { test } from "node:test";
import { ToolCallAggregator } from "../src/toolCallAggregator.ts";

/**
 * Shared between `@coder` and the custom chat panel - both drive the exact
 * same streamed OpenAI shape, and this is the one place that reassembles it.
 * A bug here is a bug in whichever surface nobody happened to test that day,
 * which is the whole reason it is one file instead of two copies.
 */

test("id, type and name arrive once; arguments arrive in fragments", () => {
  const aggregator = new ToolCallAggregator();

  aggregator.feed({
    index: 0,
    id: "call_1",
    type: "function",
    function: { name: "read_file", arguments: "" },
  });
  aggregator.feed({ index: 0, function: { arguments: '{"path"' } });
  aggregator.feed({ index: 0, function: { arguments: ': "a.ts"}' } });

  const calls = aggregator.finalize();

  assert.deepEqual(calls, [
    {
      id: "call_1",
      type: "function",
      function: { name: "read_file", arguments: '{"path": "a.ts"}' },
    },
  ]);
});

test("two parallel calls stay apart by index", () => {
  const aggregator = new ToolCallAggregator();

  aggregator.feed({
    index: 0,
    id: "call_a",
    type: "function",
    function: { name: "read_file", arguments: "{}" },
  });
  aggregator.feed({
    index: 1,
    id: "call_b",
    type: "function",
    function: { name: "list_directory", arguments: "{}" },
  });

  const calls = aggregator.finalize();

  assert.equal(calls.length, 2);
  assert.equal(calls[0].id, "call_a");
  assert.equal(calls[1].id, "call_b");
});

test("a fragment still missing its id is not a call yet", () => {
  const aggregator = new ToolCallAggregator();

  aggregator.feed({ index: 0, function: { arguments: '{"path"' } });

  assert.deepEqual(aggregator.finalize(), []);
});

test("no deltas at all is no calls, not an error", () => {
  assert.deepEqual(new ToolCallAggregator().finalize(), []);
});
