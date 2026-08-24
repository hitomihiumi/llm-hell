import { exec } from "node:child_process";
import { promisify } from "node:util";
import * as vscode from "vscode";
import { ApiError, type KnowledgeBaseClient } from "./client";
import {
  COMMAND_TIMEOUT_MS,
  commandResult,
  formatSearchResults,
  isAbsolutePath,
  truncate,
} from "./toolOutput";

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
      name: "search_knowledge_base",
      description:
        "Search the team's Google Drive, Gmail, GitLab and internal knowledge base. Use this for anything not in the open workspace: design decisions, datasheets, schedules, other repositories, past discussion. Returns a short excerpt of each result, with its id - call read_knowledge_base_result with that id to read one in full.",
      parameters: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description: "What to look for, in the words a document would use",
          },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "read_knowledge_base_result",
      description:
        "Read one search_knowledge_base result in full, by the id it printed. Use this instead of a shell command when an excerpt is not enough - a README you need whole, a file whose entire content matters.",
      parameters: {
        type: "object",
        properties: {
          id: {
            type: "string",
            description: "The id printed next to a result by search_knowledge_base",
          },
        },
        required: ["id"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "run_terminal",
      description:
        "Run a shell command in the workspace and return its output. This has no access to GitLab, Google Drive or Gmail credentials - use search_knowledge_base and read_knowledge_base_result for those instead of git, curl, gh or glab.",
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

/**
 * A path from the model, as a Uri.
 *
 * An absolute path is taken as it is; anything else is relative to the first
 * workspace folder, which is what the model means when it says `src/main.ts`.
 * `Uri.parse` used to make this decision and got it wrong twice over: it
 * reads `C:\\src` as a URI with scheme `c`, and it reads `/etc/hosts` as
 * having no scheme at all - so a POSIX absolute path was being joined onto
 * the workspace folder and quietly read from the wrong place.
 */
function resolveUri(inputPath: string): vscode.Uri {
  const path = inputPath.trim();
  if (isAbsolutePath(path)) {
    return vscode.Uri.file(path);
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  return folder ? vscode.Uri.joinPath(folder.uri, path) : vscode.Uri.file(path);
}

function workspaceFolder(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

export async function executeTool(call: ToolCall, client: KnowledgeBaseClient): Promise<string> {
  const args = parseArgs(call.function.arguments);
  if (!(await confirmTool(call, args))) {
    return `Cancelled: ${call.function.name}`;
  }

  switch (call.function.name) {
    case "search_knowledge_base": {
      // Executed here rather than on the backend, like every other tool, so
      // the agent loop has one shape. That this one happens to run by calling
      // the backend is an implementation detail of the case, not a second
      // protocol.
      const query = String(args.query ?? "").trim();
      if (!query) return "Error: no query provided.";
      try {
        // No answer: the agent is the thing that reasons over these, and
        // paying a second model to write prose it will not read is waste.
        const response = await client.search(query, { answer: false });
        return formatSearchResults(response.hits);
      } catch (error) {
        return `Error searching the knowledge base: ${(error as Error).message}`;
      }
    }
    case "read_knowledge_base_result": {
      const id = String(args.id ?? "").trim();
      if (!id) return "Error: no id provided.";
      try {
        const content = await client.content(id);
        const body = content.truncated
          ? `${content.text}\n\n[truncated — the source has more than this]`
          : content.text;
        return truncate(body || "[nothing more to read for this result]");
      } catch (error) {
        // A source with no extra content beyond its search excerpt answers
        // 404 - Gmail's search already returns the message body, for
        // instance. That is a fact about the result, not a broken tool, so
        // it is worth saying plainly rather than as a generic error.
        if (error instanceof ApiError && error.status === 404) {
          return "This result has nothing more to read beyond its search excerpt.";
        }
        return `Error reading that result: ${(error as Error).message}`;
      }
    }
    case "read_file": {
      const uri = resolveUri(String(args.path ?? ""));
      try {
        const bytes = await vscode.workspace.fs.readFile(uri);
        // Capped: a tool result goes straight into the next request, and one
        // minified bundle would spend the whole context window.
        return truncate(new TextDecoder().decode(bytes));
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
        const listing = entries.map(([name, type]) => `${name} (${fileTypeName(type)})`).join("\n");
        return truncate(listing) || "[empty directory]";
      } catch (error) {
        return `Error listing directory: ${(error as Error).message}`;
      }
    }
    case "run_terminal": {
      const command = String(args.command ?? "");
      if (!command) return "Error: no command provided.";
      try {
        const { stdout, stderr } = await execAsync(command, {
          cwd: workspaceFolder(),
          // A coding agent will eventually run a dev server. Without a
          // deadline that turn never ends, and nothing on screen says
          // whether it is working or stuck.
          timeout: COMMAND_TIMEOUT_MS,
          maxBuffer: 8 * 1024 * 1024,
          windowsHide: true,
        });
        return commandResult(stdout, stderr, { code: 0 });
      } catch (error) {
        const failure = error as Error & {
          stdout?: string;
          stderr?: string;
          code?: number;
          killed?: boolean;
          signal?: string;
        };
        return commandResult(failure.stdout ?? "", failure.stderr ?? "", {
          code: failure.code ?? "?",
          // `killed` with no exit code is how `exec` reports its own timeout.
          timedOut: Boolean(failure.killed) && failure.code === undefined,
          message: failure.message,
        });
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
