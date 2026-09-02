import assert from "node:assert/strict";
import { test } from "node:test";
import {
  flattenToolResult,
  isJsonRpcResponse,
  lastResponseFromSse,
  notification,
  publishedNames,
  request,
  toToolDefinition,
} from "../src/mcpProtocol.ts";

// --- envelopes -----------------------------------------------------------------

test("a request with params carries them", () => {
  assert.deepEqual(request(1, "tools/list", { cursor: "a" }), {
    jsonrpc: "2.0",
    id: 1,
    method: "tools/list",
    params: { cursor: "a" },
  });
});

test("a request with no params omits the field rather than sending null", () => {
  /* Some servers reject a `params` field they were not expecting; the spec
     makes it optional, so a call with nothing to say omits it. */
  assert.deepEqual(request(1, "tools/list"), { jsonrpc: "2.0", id: 1, method: "tools/list" });
});

test("a notification carries no id", () => {
  assert.deepEqual(notification("notifications/initialized"), {
    jsonrpc: "2.0",
    method: "notifications/initialized",
  });
});

test("isJsonRpcResponse accepts a real response", () => {
  assert.ok(isJsonRpcResponse({ jsonrpc: "2.0", id: 1, result: {} }));
  assert.ok(isJsonRpcResponse({ jsonrpc: "2.0", id: 1, error: { code: -1, message: "no" } }));
});

test("isJsonRpcResponse rejects a notification and non-messages", () => {
  /* A server-to-client notification (no `id`) is not a response to
     anything this waits on - settling a pending call on one would resolve
     the wrong promise with the wrong value. */
  assert.ok(!isJsonRpcResponse({ jsonrpc: "2.0", method: "notifications/progress" }));
  assert.ok(!isJsonRpcResponse(null));
  assert.ok(!isJsonRpcResponse("not an object"));
  assert.ok(!isJsonRpcResponse({ jsonrpc: "2.0", id: "not-a-number" }));
});

// --- toToolDefinition ------------------------------------------------------------

test("an MCP tool's inputSchema becomes the tool definition's parameters unchanged", () => {
  const def = toToolDefinition("mcp_fs_read_file", {
    name: "read_file",
    description: "Read a file",
    inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
  });
  assert.equal(def.function.name, "mcp_fs_read_file");
  assert.equal(def.function.description, "Read a file");
  assert.deepEqual(def.function.parameters, {
    type: "object",
    properties: { path: { type: "string" } },
    required: ["path"],
  });
});

test("a tool with no inputSchema still gets a valid, empty one", () => {
  /* A tool-calling model reading `properties` absent is not the same as
     `{}` - one says "this tool's arguments are undocumented", the other
     says "this tool takes no arguments", and a server is allowed to send
     neither. */
  const def = toToolDefinition("mcp_x_y", { name: "y" });
  assert.deepEqual(def.function.parameters, { type: "object", properties: {}, required: [] });
});

test("a tool with no description gets a fallback rather than an empty string", () => {
  const def = toToolDefinition("mcp_x_y", { name: "y" });
  assert.ok(def.function.description.length > 0);
});

// --- publishedNames --------------------------------------------------------------

test("published names carry the server and tool name", () => {
  const names = publishedNames("filesystem", [{ name: "read_file" }, { name: "write_file" }]);
  assert.equal(names.get("read_file"), "mcp_filesystem_read_file");
  assert.equal(names.get("write_file"), "mcp_filesystem_write_file");
});

test("characters a tool-calling model's name pattern rejects are sanitised", () => {
  const names = publishedNames("my server!", [{ name: "do thing" }]);
  const published = names.get("do thing");
  assert.ok(published);
  assert.match(published as string, /^[a-zA-Z0-9_-]+$/);
});

test("two tools that sanitise to the same name get distinct published names", () => {
  const names = publishedNames("s", [{ name: "a.b" }, { name: "a-b" }]);
  const values = [...names.values()];
  assert.equal(new Set(values).size, 2, "must not collide");
});

test("every published name stays within a typical tool-name length limit", () => {
  const longServer = "a".repeat(80);
  const longTool = "b".repeat(80);
  const names = publishedNames(longServer, [{ name: longTool }]);
  const published = names.get(longTool) as string;
  assert.ok(published.length <= 64, `${published.length} chars`);
});

// --- flattenToolResult -----------------------------------------------------------

test("a single text block becomes the whole string", () => {
  assert.equal(flattenToolResult({ content: [{ type: "text", text: "42" }] }), "42");
});

test("several text blocks are joined", () => {
  assert.equal(
    flattenToolResult({
      content: [
        { type: "text", text: "line one" },
        { type: "text", text: "line two" },
      ],
    }),
    "line one\nline two",
  );
});

test("a non-text block is named, not dropped silently", () => {
  /* A coding model told nothing came back would retry the same call. */
  const text = flattenToolResult({ content: [{ type: "image", data: "..." }] });
  assert.match(text, /image/);
});

test("isError prefixes the result so a model reads it as a failure", () => {
  const text = flattenToolResult({ content: [{ type: "text", text: "not found" }], isError: true });
  assert.match(text, /^Error:/);
  assert.match(text, /not found/);
});

test("no content at all still returns something readable", () => {
  assert.equal(flattenToolResult({}), "[tool returned nothing]");
  assert.equal(flattenToolResult(undefined), "[tool returned nothing]");
});

// --- lastResponseFromSse -----------------------------------------------------------

test("a single SSE event carrying the response", () => {
  const body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n';
  assert.deepEqual(lastResponseFromSse(body), {
    jsonrpc: "2.0",
    id: 1,
    result: { ok: true },
  });
});

test("a progress notification ahead of the real response is skipped", () => {
  /* A streamable-HTTP call can send several events before the one that
     answers it - the notification has no `id` and must not be mistaken for
     the response. */
  const body = [
    'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}',
    "",
    'data: {"jsonrpc":"2.0","id":7,"result":{"tools":[]}}',
    "",
  ].join("\n");
  assert.deepEqual(lastResponseFromSse(body), {
    jsonrpc: "2.0",
    id: 7,
    result: { tools: [] },
  });
});

test("a multi-line data field is joined before parsing", () => {
  /* The SSE spec allows a single event's data to arrive as several `data:`
     lines, joined with a newline before the field is used. */
  const body = ['data: {"jsonrpc":"2.0","id":1,', 'data: "result":{"ok":true}}', ""].join("\n");
  assert.deepEqual(lastResponseFromSse(body), { jsonrpc: "2.0", id: 1, result: { ok: true } });
});

test("a keep-alive comment is not mistaken for a message", () => {
  const body = [": keep-alive", "", 'data: {"jsonrpc":"2.0","id":1,"result":{}}', ""].join("\n");
  assert.deepEqual(lastResponseFromSse(body), { jsonrpc: "2.0", id: 1, result: {} });
});

test("nothing parseable returns undefined rather than throwing", () => {
  assert.equal(lastResponseFromSse(""), undefined);
  assert.equal(lastResponseFromSse(": just a comment\n\n"), undefined);
});
