import { type ChildProcess, spawn } from "node:child_process";
import { statSync } from "node:fs";
import { delimiter, join } from "node:path";
import * as vscode from "vscode";
import {
  flattenToolResult,
  isJsonRpcResponse,
  type JsonRpcResponse,
  lastResponseFromSse,
  MCP_PROTOCOL_VERSION,
  type McpToolInfo,
  notification,
  publishedNames,
  request,
  toToolDefinition,
} from "./mcpProtocol";
import { type CommandLookup, planSpawn } from "./spawnCommand";
import type { ToolDefinition } from "./tools";

/**
 * Custom MCP servers, wired into the `@coder`/agent tool loop alongside the
 * fixed tools in `tools.ts`.
 *
 * Configured the way every other MCP client configures them - a name, and
 * either a local command or a remote URL - in `knowledgeBase.mcp.servers`, so
 * an entry copied from Claude Desktop's or Cursor's config needs no
 * translation. Connected lazily on the first turn that needs a tool list, one
 * connection per server for the life of the window, torn down and reconnected
 * only when the setting itself changes or a command asks for it.
 *
 * No dependency on an MCP SDK: this extension carries none at all (see
 * `tools.ts`'s own docstring on `highlight.ts`), and what a tool-calling loop
 * actually needs of MCP - `initialize`, `tools/list`, `tools/call` - is a
 * page of JSON-RPC, not a library.
 */

export interface McpServerConfig {
  /** A local server: spawned as `command args...`, stdio framed as MCP's stdio transport (newline-delimited JSON, no embedded newlines). */
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  /** A remote server: MCP's streamable-HTTP transport - one POST per call. */
  url?: string;
  headers?: Record<string, string>;
  disabled?: boolean;
}

const CALL_TIMEOUT_MS = 60_000;

// The handshake gets longer, because the first one pays for the server's own
// installation: `npx -y <package>` downloads before it runs a line, and a cold
// fetch on a slow network outlasts a call timeout easily. Timing out there
// looks exactly like the failure this whole file exists to avoid - no tools
// published, and a model answering "I don't have that tool".
const HANDSHAKE_TIMEOUT_MS = 180_000;

const output = vscode.window.createOutputChannel("Knowledge Base: MCP");

export function mcpOutputChannel(): vscode.OutputChannel {
  return output;
}

function nodeLookup(): CommandLookup {
  return {
    windows: process.platform === "win32",
    path: process.env.PATH ?? process.env.Path ?? "",
    pathExt: process.env.PATHEXT ?? ".COM;.EXE;.BAT;.CMD",
    exists: (candidate) => {
      try {
        return statSync(candidate).isFile();
      } catch {
        return false;
      }
    },
    separator: delimiter,
    join,
  };
}

/** Servers already complained about this session, so a failing one is said once rather than every turn. */
const reported = new Set<string>();

/**
 * A server that did not come up, said out loud.
 *
 * The log alone was not enough, and the way it failed is why: a server that
 * never starts publishes no tools, so the model simply answers "I don't have
 * that tool" - indistinguishable from the feature not existing. Nothing about
 * that points at an output channel, so the notification does.
 */
function reportFailure(name: string, message: string): void {
  output.appendLine(`[${name}] ${message}`);
  if (reported.has(name)) return;
  reported.add(name);
  vscode.window
    .showWarningMessage(`MCP server "${name}" did not start: ${message}`, "Show log")
    .then((choice) => {
      if (choice === "Show log") output.show();
    });
}

export function readMcpServers(): Record<string, McpServerConfig> {
  return vscode.workspace
    .getConfiguration("knowledgeBase")
    .get<Record<string, McpServerConfig>>("mcp.servers", {});
}

/**
 * An object-valued setting is not deep-merged across scopes: a workspace
 * value, if there is one, replaces the user value entirely rather than being
 * combined with it key by key. Both helpers below exist because of that -
 * editing one server has to read and write back the same scope it came from,
 * or writing the merged view to the wrong scope would leave a stale copy
 * shadowing the edit, and a "remove" would look like it did nothing.
 */

/** The servers already saved at exactly this scope - not the merged view a lower scope might be shadowing. */
export function mcpServersAtScope(
  target: vscode.ConfigurationTarget,
): Record<string, McpServerConfig> {
  const inspected = vscode.workspace
    .getConfiguration("knowledgeBase")
    .inspect<Record<string, McpServerConfig>>("mcp.servers");
  if (target === vscode.ConfigurationTarget.Workspace) return inspected?.workspaceValue ?? {};
  return inspected?.globalValue ?? {};
}

/** Which scope a configured server actually lives in, and that scope's own value. */
export function mcpServerScope(name: string): {
  target: vscode.ConfigurationTarget;
  value: Record<string, McpServerConfig>;
} {
  const inspected = vscode.workspace
    .getConfiguration("knowledgeBase")
    .inspect<Record<string, McpServerConfig>>("mcp.servers");
  if (inspected?.workspaceValue && name in inspected.workspaceValue) {
    return { target: vscode.ConfigurationTarget.Workspace, value: inspected.workspaceValue };
  }
  return { target: vscode.ConfigurationTarget.Global, value: inspected?.globalValue ?? {} };
}

export async function writeMcpServers(
  servers: Record<string, McpServerConfig>,
  target: vscode.ConfigurationTarget = vscode.ConfigurationTarget.Global,
): Promise<void> {
  await vscode.workspace.getConfiguration("knowledgeBase").update("mcp.servers", servers, target);
}

interface Transport {
  request(method: string, params?: unknown, timeoutMs?: number): Promise<unknown>;
  notify(method: string, params?: unknown): void;
  dispose(): void;
}

/**
 * One server's stdio pipe, framed as MCP's stdio transport: each message is
 * one line of JSON, in either direction, and a message never contains a
 * literal newline.
 */
class StdioTransport implements Transport {
  private readonly proc: ChildProcess;
  private buffer = "";
  private nextId = 1;
  private readonly pending = new Map<
    number,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >();

  constructor(name: string, config: McpServerConfig, cwd: string | undefined) {
    if (!config.command) throw new Error("no command configured");
    const plan = planSpawn(config.command, config.args ?? [], nodeLookup());
    output.appendLine(
      `[${name}] starting: ${plan.file}${plan.args.length ? ` ${plan.args.join(" ")}` : ""}${plan.shell ? " (via shell)" : ""}`,
    );
    this.proc = spawn(plan.file, plan.args, {
      cwd,
      env: { ...process.env, ...config.env },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      shell: plan.shell,
    });
    this.proc.stdout?.setEncoding("utf8").on("data", (chunk: string) => this.onData(chunk));
    this.proc.stderr?.setEncoding("utf8").on("data", (chunk: string) => {
      output.appendLine(`[${name}] ${chunk.trimEnd()}`);
    });
    this.proc.on("exit", (code, signal) => {
      output.appendLine(`[${name}] exited (code=${code}, signal=${signal})`);
      const error = new Error(`MCP server "${name}" exited (code=${code})`);
      for (const { reject } of this.pending.values()) reject(error);
      this.pending.clear();
    });
    this.proc.on("error", (error) => {
      reportFailure(name, `failed to start: ${error.message}`);
      for (const { reject } of this.pending.values()) reject(error);
      this.pending.clear();
    });
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline !== -1) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      newline = this.buffer.indexOf("\n");
      if (!line) continue;
      let message: unknown;
      try {
        message = JSON.parse(line);
      } catch {
        continue; // A server that logs to stdout instead of stderr. Not our line to read.
      }
      if (isJsonRpcResponse(message)) this.settle(message);
    }
  }

  private settle(response: JsonRpcResponse): void {
    const waiting = this.pending.get(response.id);
    if (!waiting) return;
    this.pending.delete(response.id);
    if (response.error) waiting.reject(new Error(response.error.message));
    else waiting.resolve(response.result);
  }

  request(method: string, params?: unknown, timeoutMs = CALL_TIMEOUT_MS): Promise<unknown> {
    const id = this.nextId++;
    const message = request(id, method, params);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`MCP request "${method}" timed out`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      this.proc.stdin?.write(`${JSON.stringify(message)}\n`);
    });
  }

  notify(method: string, params?: unknown): void {
    this.proc.stdin?.write(`${JSON.stringify(notification(method, params))}\n`);
  }

  dispose(): void {
    for (const { reject } of this.pending.values()) reject(new Error("MCP connection closed"));
    this.pending.clear();
    this.proc.kill();
  }
}

/**
 * One remote server, spoken to over MCP's streamable-HTTP transport: every
 * request is its own POST, answered either as a plain JSON body or as one
 * SSE event carrying the same JSON-RPC response.
 */
class HttpTransport implements Transport {
  private nextId = 1;
  private sessionId: string | undefined;

  constructor(
    private readonly name: string,
    private readonly config: McpServerConfig,
  ) {
    if (!config.url) throw new Error("no url configured");
  }

  async request(method: string, params?: unknown, timeoutMs = CALL_TIMEOUT_MS): Promise<unknown> {
    const id = this.nextId++;
    const response = await this.post(request(id, method, params), timeoutMs);
    if (!response) throw new Error(`MCP server "${this.name}" sent no response to "${method}"`);
    if (response.error) throw new Error(response.error.message);
    return response.result;
  }

  async notify(method: string, params?: unknown): Promise<void> {
    await this.post(notification(method, params));
  }

  private async post(
    message: unknown,
    timeoutMs = CALL_TIMEOUT_MS,
  ): Promise<JsonRpcResponse | undefined> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(this.config.url as string, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json, text/event-stream",
          ...this.config.headers,
          ...(this.sessionId ? { "mcp-session-id": this.sessionId } : {}),
        },
        body: JSON.stringify(message),
        signal: controller.signal,
      });
      const sessionId = res.headers.get("mcp-session-id");
      if (sessionId) this.sessionId = sessionId;
      if (res.status === 202) return undefined; // Accepted, no body - a notification's own answer.
      if (!res.ok) throw new Error(`HTTP ${res.status} from "${this.name}"`);

      const contentType = res.headers.get("content-type") ?? "";
      const body = await res.text();
      if (contentType.includes("text/event-stream")) return lastResponseFromSse(body);
      return body ? (JSON.parse(body) as JsonRpcResponse) : undefined;
    } finally {
      clearTimeout(timer);
    }
  }

  dispose(): void {
    // Stateless enough for a tool-calling loop: nothing to close between calls.
  }
}

class McpConnection {
  private readonly transport: Transport;
  private ready: Promise<McpToolInfo[]> | undefined;
  readonly names: Map<string, string> = new Map(); // MCP tool name -> published name

  constructor(
    readonly name: string,
    config: McpServerConfig,
    cwd: string | undefined,
  ) {
    this.transport = config.url
      ? new HttpTransport(name, config)
      : new StdioTransport(name, config, cwd);
  }

  /** Connects and lists tools on first use, and only once - a reconnect needs a fresh `McpConnection`. */
  tools(): Promise<McpToolInfo[]> {
    if (!this.ready) {
      this.ready = this.handshake().catch((error: Error) => {
        reportFailure(this.name, error.message);
        return [];
      });
    }
    return this.ready;
  }

  private async handshake(): Promise<McpToolInfo[]> {
    await this.transport.request(
      "initialize",
      {
        protocolVersion: MCP_PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: "llmhell-knowledge-base", version: "0.1.0" },
      },
      HANDSHAKE_TIMEOUT_MS,
    );
    this.transport.notify("notifications/initialized");
    const result = (await this.transport.request("tools/list")) as { tools?: McpToolInfo[] };
    const tools = result.tools ?? [];
    for (const [tool, published] of publishedNames(this.name, tools))
      this.names.set(tool, published);
    return tools;
  }

  async call(toolName: string, args: Record<string, unknown>): Promise<string> {
    const result = await this.transport.request("tools/call", { name: toolName, arguments: args });
    return flattenToolResult(result);
  }

  dispose(): void {
    this.transport.dispose();
  }
}

let connections: Map<string, McpConnection> | undefined;
let assembled:
  | Promise<{ definitions: ToolDefinition[]; dispatch: Map<string, McpConnection> }>
  | undefined;

function buildConnections(): Map<string, McpConnection> {
  const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const map = new Map<string, McpConnection>();
  for (const [name, config] of Object.entries(readMcpServers())) {
    if (config.disabled) continue;
    if (!config.command && !config.url) {
      output.appendLine(`[${name}] skipped: neither "command" nor "url" is set`);
      continue;
    }
    try {
      map.set(name, new McpConnection(name, config, cwd));
    } catch (error) {
      output.appendLine(`[${name}] ${(error as Error).message}`);
    }
  }
  return map;
}

/**
 * Every configured, enabled server's tools, published under a name unique
 * across all of them, plus what dispatching a call by that name needs.
 *
 * Cached for the window's lifetime (see `reloadMcpServers`) - a coder turn
 * calls this every round, and reconnecting per turn would mean a fresh
 * `npx` cold start for every tool call in an agent loop.
 */
export function mcpTools(): Promise<{
  definitions: ToolDefinition[];
  dispatch: Map<string, McpConnection>;
}> {
  if (!assembled) {
    connections ??= buildConnections();
    const conns = connections;
    assembled = (async () => {
      const definitions: ToolDefinition[] = [];
      const dispatch = new Map<string, McpConnection>();
      await Promise.all(
        Array.from(conns.values()).map(async (conn) => {
          const tools = await conn.tools();
          for (const tool of tools) {
            const published = conn.names.get(tool.name);
            if (!published) continue;
            definitions.push(toToolDefinition(published, tool));
            dispatch.set(published, conn);
          }
        }),
      );
      return { definitions, dispatch };
    })();
  }
  return assembled;
}

/** Whether `name` is a published MCP tool - `executeTool` uses this to route to `callMcpTool` instead of its fixed switch, and `tools.ts`'s approval policy uses it to treat every MCP tool as one whose effects are unknown. */
export async function isMcpTool(name: string): Promise<boolean> {
  return (await mcpTools()).dispatch.has(name);
}

export async function callMcpTool(name: string, argsJson: string): Promise<string | undefined> {
  const { dispatch } = await mcpTools();
  const conn = dispatch.get(name);
  if (!conn) return undefined;
  let args: Record<string, unknown>;
  try {
    args = JSON.parse(argsJson) as Record<string, unknown>;
  } catch {
    args = {};
  }
  // The MCP tool's own name on the wire, not the published one this map key is - the two
  // differ whenever sanitising or de-duplicating changed it.
  const original = [...conn.names.entries()].find(([, published]) => published === name)?.[0];
  try {
    return await conn.call(original ?? name, args);
  } catch (error) {
    return `Error calling ${name}: ${(error as Error).message}`;
  }
}

/** Disconnects every server and forgets the cached tool list - the setting changed, or the user asked for a fresh start after editing one. */
export function reloadMcpServers(): void {
  for (const conn of connections?.values() ?? []) conn.dispose();
  connections = undefined;
  assembled = undefined;
  // A reload is someone acting on the last failure - the next one is news again.
  reported.clear();
}

export function disposeMcpServers(): void {
  reloadMcpServers();
}
