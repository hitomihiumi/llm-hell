import type { ToolDefinition } from "./tools";

/**
 * The pure half of talking to a custom MCP server: JSON-RPC envelopes, tool
 * publishing names, and turning a `tools/call` result into a string.
 *
 * Split from `src/mcp.ts` for the reason `toolOutput.ts` is split from
 * `tools.ts` - this needs no process, no socket and no `vscode`, so it is the
 * part worth a test that runs without an extension host.
 */

export const MCP_PROTOCOL_VERSION = "2024-11-05";

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: unknown;
}

export interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

export interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export function isJsonRpcResponse(value: unknown): value is JsonRpcResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "jsonrpc" in value &&
    "id" in value &&
    typeof (value as { id: unknown }).id === "number"
  );
}

/**
 * The last JSON-RPC response carried in a streamable-HTTP body sent as
 * `text/event-stream` - a stream may also carry progress notifications
 * first, which have no `id` and are not it.
 */
export function lastResponseFromSse(body: string): JsonRpcResponse | undefined {
  let found: JsonRpcResponse | undefined;
  for (const block of body.split("\n\n")) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim())
      .join("\n");
    if (!data) continue;
    try {
      const message = JSON.parse(data);
      if (isJsonRpcResponse(message)) found = message;
    } catch {
      // A comment or a keep-alive ping, not a message.
    }
  }
  return found;
}

export function request(id: number, method: string, params?: unknown): JsonRpcRequest {
  return params === undefined
    ? { jsonrpc: "2.0", id, method }
    : { jsonrpc: "2.0", id, method, params };
}

export function notification(method: string, params?: unknown): JsonRpcNotification {
  return params === undefined ? { jsonrpc: "2.0", method } : { jsonrpc: "2.0", method, params };
}

/** What `tools/list` hands back for one tool, as much of it as this needs. */
export interface McpToolInfo {
  name: string;
  description?: string;
  inputSchema?: {
    type?: string;
    properties?: Record<string, unknown>;
    required?: string[];
  };
}

/**
 * An MCP tool, dressed as one of ours.
 *
 * MCP's `inputSchema` already is the JSON Schema object our own
 * `ToolDefinition.function.parameters` wants - no translation, only a
 * fallback for a server that omits one, which the spec allows and a
 * tool-calling model does not: `properties`/`required` absent is not the
 * same as `{}` to a model reading the schema for what it may pass.
 */
export function toToolDefinition(publishedName: string, tool: McpToolInfo): ToolDefinition {
  return {
    type: "function",
    function: {
      name: publishedName,
      description: tool.description || `MCP tool ${tool.name}`,
      parameters: {
        type: "object",
        properties: tool.inputSchema?.properties ?? {},
        required: tool.inputSchema?.required ?? [],
      },
    },
  };
}

/**
 * Every published name this server's tools get, keyed by the server's own
 * tool name - built once per connection rather than parsed back out of the
 * name later, because a server or tool name containing the separator would
 * make that parse ambiguous. The caller keeps the map; this only decides what
 * the names look like.
 *
 * `mcp_<server>_<tool>`, sanitised to what a tool-calling model's `name`
 * pattern accepts (`^[a-zA-Z0-9_-]+$` across every provider this project
 * talks to) and de-duplicated - two servers whose sanitised names collide, or
 * a tool name repeated on one server, get a numeric suffix rather than
 * silently shadowing each other.
 */
export function publishedNames(serverName: string, tools: McpToolInfo[]): Map<string, string> {
  const prefix = `mcp_${sanitise(serverName)}_`;
  const used = new Set<string>();
  const names = new Map<string, string>();
  for (const tool of tools) {
    const base = `${prefix}${sanitise(tool.name)}`.slice(0, 60);
    let name = base;
    let suffix = 2;
    while (used.has(name)) {
      name = `${base.slice(0, 60 - String(suffix).length - 1)}_${suffix}`;
      suffix += 1;
    }
    used.add(name);
    names.set(tool.name, name);
  }
  return names;
}

function sanitise(name: string): string {
  return name.replace(/[^a-zA-Z0-9_-]/g, "_") || "server";
}

/** One block of a `tools/call` result's `content` array. */
export interface McpContentBlock {
  type: string;
  text?: string;
  [key: string]: unknown;
}

/**
 * `tools/call`'s result, as the one string every tool in this extension
 * returns.
 *
 * Text blocks are joined as they came - a server may legitimately send
 * several. Anything else (an image, an embedded resource) is named rather
 * than dropped silently: a coding model told nothing came back would retry
 * the same call, where being told "this tool also returned an image, which
 * cannot be shown here" is a fact it can act on.
 */
export function flattenToolResult(result: unknown): string {
  const payload = result as { content?: McpContentBlock[]; isError?: boolean } | undefined;
  const blocks = payload?.content ?? [];
  const parts: string[] = [];
  for (const block of blocks) {
    if (block.type === "text" && typeof block.text === "string") {
      parts.push(block.text);
    } else {
      parts.push(`[${block.type} content, not shown]`);
    }
  }
  const text = parts.join("\n").trim();
  const body = text || "[tool returned nothing]";
  return payload?.isError ? `Error: ${body}` : body;
}
