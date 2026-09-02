import * as vscode from "vscode";
import {
  type McpServerConfig,
  mcpServerScope,
  mcpServersAtScope,
  readMcpServers,
  reloadMcpServers,
  writeMcpServers,
} from "./mcp";

/**
 * Adding, disabling and removing custom MCP servers, from the command
 * palette rather than only by hand-editing `settings.json`.
 *
 * The setting itself (`knowledgeBase.mcp.servers`) is a plain object keyed by
 * name, in the same shape every other MCP client uses - a `command`+`args`
 * server or a `url` server - so an entry copied from Claude Desktop's or
 * Cursor's config drops in unedited. This is the guided path for someone
 * typing a new one in for the first time; `settings.json` remains the faster
 * one for anybody who already knows the shape, headers included - the
 * guided flow does not ask for those, on the theory that a server needing a
 * bearer token is already a settings.json kind of task.
 */

const ADD_NEW = Symbol("add");

export async function manageMcpServers(): Promise<void> {
  const servers = readMcpServers();
  const entries = Object.entries(servers);

  const picks: Array<vscode.QuickPickItem & { name: typeof ADD_NEW | string }> = [
    { label: "$(add) Add MCP server…", name: ADD_NEW },
    ...entries.map(([name, config]) => ({
      label: `${config.disabled ? "$(circle-slash)" : "$(check)"} ${name}`,
      description: describeServer(config),
      name,
    })),
  ];

  const chosen = await vscode.window.showQuickPick(picks, {
    title: "MCP servers the coding agent can call",
    placeHolder: entries.length ? undefined : "None configured yet",
  });
  if (!chosen) return;

  if (chosen.name === ADD_NEW) {
    await addServer(servers);
    return;
  }

  await manageOne(servers, chosen.name);
}

function describeServer(config: McpServerConfig): string {
  const where = config.command
    ? `${config.command}${config.args?.length ? ` ${config.args.join(" ")}` : ""}`
    : (config.url ?? "");
  return config.disabled ? `${where} (disabled)` : where;
}

async function manageOne(servers: Record<string, McpServerConfig>, name: string): Promise<void> {
  const config = servers[name];
  const action = await vscode.window.showQuickPick(
    [
      config.disabled ? "Enable" : "Disable",
      "Remove",
      config.command ? "Copy command" : "Copy URL",
    ],
    { title: name },
  );
  if (!action) return;

  if (action === "Remove") {
    const confirmed = await vscode.window.showWarningMessage(
      `Remove the MCP server "${name}"?`,
      { modal: true },
      "Remove",
    );
    if (confirmed !== "Remove") return;
    const { target, value } = mcpServerScope(name);
    const { [name]: _removed, ...rest } = value;
    await save(rest, target);
    return;
  }

  if (action === "Enable" || action === "Disable") {
    const { target, value } = mcpServerScope(name);
    await save({ ...value, [name]: { ...value[name], disabled: action === "Disable" } }, target);
    return;
  }

  await vscode.env.clipboard.writeText(config.command ?? config.url ?? "");
}

async function addServer(servers: Record<string, McpServerConfig>): Promise<void> {
  const picked = await vscode.window.showQuickPick(
    [
      {
        label: "Local command",
        description: "Spawned as a subprocess, MCP over stdio",
        transport: "stdio" as const,
      },
      {
        label: "Remote URL",
        description: "MCP over streamable HTTP",
        transport: "http" as const,
      },
    ],
    { title: "How does this MCP server run?" },
  );
  if (!picked) return;

  const name = await vscode.window.showInputBox({
    title: "Server name",
    placeHolder: "e.g. filesystem",
    validateInput: (value) => {
      const trimmed = value.trim();
      if (!trimmed) return "Required.";
      if (!/^[\w.-]+$/.test(trimmed)) return "Letters, digits, _, - and . only.";
      return undefined;
    },
  });
  if (!name) return;
  const key = name.trim();
  if (servers[key]) {
    const overwrite = await vscode.window.showWarningMessage(
      `"${key}" is already configured. Replace it?`,
      { modal: true },
      "Replace",
    );
    if (overwrite !== "Replace") return;
  }

  const config: McpServerConfig | undefined =
    picked.transport === "stdio" ? await promptStdio() : await promptHttp();
  if (!config) return;

  const scope = await vscode.window.showQuickPick(
    [
      { label: "This workspace", target: vscode.ConfigurationTarget.Workspace },
      { label: "All workspaces (user settings)", target: vscode.ConfigurationTarget.Global },
    ],
    { title: "Save this server for" },
  );
  if (!scope) return;

  // Merged into what this scope already holds, not the merged view a lower
  // scope may be shadowing - saving to Workspace must not fork every
  // already-global server into the workspace's own settings.json.
  const existing = mcpServersAtScope(scope.target);
  await save({ ...existing, [key]: config }, scope.target);
  vscode.window.showInformationMessage(`Added MCP server "${key}".`);
}

async function promptStdio(): Promise<McpServerConfig | undefined> {
  const command = await vscode.window.showInputBox({
    title: "Command",
    placeHolder: "npx",
    ignoreFocusOut: true,
  });
  if (!command) return undefined;

  const argsLine = await vscode.window.showInputBox({
    title: "Arguments (space-separated)",
    placeHolder: "-y @modelcontextprotocol/server-filesystem /path/to/allow",
    ignoreFocusOut: true,
  });
  const args = (argsLine ?? "").trim().split(/\s+/).filter(Boolean);
  return { command: command.trim(), args };
}

async function promptHttp(): Promise<McpServerConfig | undefined> {
  const url = await vscode.window.showInputBox({
    title: "Server URL",
    placeHolder: "https://example.com/mcp",
    ignoreFocusOut: true,
    validateInput: (value) => (/^https?:\/\//.test(value.trim()) ? undefined : "Needs http(s)://"),
  });
  if (!url) return undefined;
  return { url: url.trim() };
}

async function save(
  servers: Record<string, McpServerConfig>,
  target: vscode.ConfigurationTarget = vscode.ConfigurationTarget.Global,
): Promise<void> {
  await writeMcpServers(servers, target);
  reloadMcpServers();
}
