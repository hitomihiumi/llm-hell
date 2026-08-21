import { exec } from "node:child_process";
import { promisify } from "node:util";
import * as vscode from "vscode";

/**
 * The tools the `@coder` agent can call in the local workspace.
 *
 * These are executed in the extension host, not on the backend, because the
 * files and terminal live on the user's machine. The backend only decides
 * which tool to call and with what arguments.
 */

const execAsync = promisify(exec);

export interface ToolDefinition {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: {
      type: "object";
      properties: Record<string, unknown>;
      required: string[];
    };
  };
}

export interface ToolCall {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string;
  };
}

export const TOOLS: ToolDefinition[] = [
  {
    type: "function",
    function: {
      name: "read_file",
      description: "Read the contents of a file in the workspace.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Relative or absolute file path" },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description:
        "Write content to a file in the workspace. Creates the file if it does not exist.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Relative or absolute file path" },
          content: { type: "string", description: "Full content to write" },
        },
        required: ["path", "content"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "list_directory",
      description: "List files and directories at a workspace path.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Relative or absolute directory path" },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "run_terminal",
      description: "Run a shell command in the workspace and return its output.",
      parameters: {
        type: "object",
        properties: {
          command: { type: "string", description: "Shell command to run" },
        },
        required: ["command"],
      },
    },
  },
];

function resolveUri(inputPath: string): vscode.Uri {
  if (vscode.Uri.parse(inputPath).scheme) {
    return vscode.Uri.file(inputPath);
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    return vscode.Uri.file(inputPath);
  }
  return vscode.Uri.joinPath(folder.uri, inputPath);
}

function workspaceFolder(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

export async function executeTool(call: ToolCall): Promise<string> {
  const args = parseArgs(call.function.arguments);
  if (!(await confirmTool(call, args))) {
    return `Cancelled: ${call.function.name}`;
  }

  switch (call.function.name) {
    case "read_file": {
      const uri = resolveUri(String(args.path ?? ""));
      try {
        const bytes = await vscode.workspace.fs.readFile(uri);
        return new TextDecoder().decode(bytes);
      } catch (error) {
        return `Error reading file: ${(error as Error).message}`;
      }
    }
    case "write_file": {
      const uri = resolveUri(String(args.path ?? ""));
      const content = String(args.content ?? "");
      try {
        await vscode.workspace.fs.writeFile(uri, new TextEncoder().encode(content));
        return `Wrote ${content.length} bytes to ${String(args.path)}.`;
      } catch (error) {
        return `Error writing file: ${(error as Error).message}`;
      }
    }
    case "list_directory": {
      const uri = resolveUri(String(args.path ?? ""));
      try {
        const entries = await vscode.workspace.fs.readDirectory(uri);
        return entries.map(([name, type]) => `${name} (${fileTypeName(type)})`).join("\n");
      } catch (error) {
        return `Error listing directory: ${(error as Error).message}`;
      }
    }
    case "run_terminal": {
      const command = String(args.command ?? "");
      if (!command) return "Error: no command provided.";
      try {
        const { stdout, stderr } = await execAsync(command, { cwd: workspaceFolder() });
        return [stdout, stderr].filter(Boolean).join("\n");
      } catch (error) {
        const execError = error as Error & { stdout?: string; stderr?: string; code?: number };
        return `Exit ${execError.code ?? "?"}: ${execError.message}\n${execError.stdout ?? ""}\n${execError.stderr ?? ""}`;
      }
    }
    default:
      return `Unknown tool: ${call.function.name}`;
  }
}

async function confirmTool(call: ToolCall, args: Record<string, unknown>): Promise<boolean> {
  const confirm = vscode.workspace
    .getConfiguration("knowledgeBase")
    .get<boolean>("coder.confirmTools", true);
  if (!confirm) return true;
  if (call.function.name !== "write_file" && call.function.name !== "run_terminal") return true;

  const summary =
    call.function.name === "write_file"
      ? `Write to ${String(args.path ?? "unknown")}`
      : `Run command: ${String(args.command ?? "")}`;
  const answer = await vscode.window.showWarningMessage(
    `@coder wants to ${summary}`,
    { modal: true },
    "Allow",
  );
  return answer === "Allow";
}

function parseArgs(raw: string): Record<string, unknown> {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function fileTypeName(type: vscode.FileType): string {
  switch (type) {
    case vscode.FileType.File:
      return "file";
    case vscode.FileType.Directory:
      return "directory";
    case vscode.FileType.SymbolicLink:
      return "symlink";
    default:
      return "unknown";
  }
}
