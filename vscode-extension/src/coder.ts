import * as vscode from "vscode";
import { AuthError, type ChatMessage, type KnowledgeBaseClient } from "./client";
import { collapse, historyFor } from "./format";
import { executeTool, TOOLS, type ToolCall } from "./tools";

/**
 * `@coder` in the chat panel.
 *
 * Where `@kb` searches the corpus and cites what it found, `@coder` asks the
 * same backend to act as an OpenAI-compatible coding model. The backend still
 * runs its RAG pipeline, but the conversation is carried in the standard
 * chat-completion format so it feels like any other coding assistant.
 *
 * Tool mode: when the backend returns `tool_calls`, this participant executes
 * the matching local tools (read/write files, list directories, run shell
 * commands) and sends the results back, looping until the model produces a
 * final answer.
 */

export const PARTICIPANT_ID = "knowledgeBase.coder";

const MAX_AGENT_TURNS = 10;

interface AgentTurn {
  content: string;
  toolCalls: ToolCall[];
}

export function registerCoderParticipant(
  _context: vscode.ExtensionContext,
  client: KnowledgeBaseClient,
): vscode.ChatParticipant {
  const participant = vscode.chat.createChatParticipant(
    PARTICIPANT_ID,
    async (request, chatContext, stream, token): Promise<vscode.ChatResult> => {
      const question = request.prompt.trim();
      if (!question) {
        stream.markdown("Ask a coding question.");
        return {};
      }

      const abort = new AbortController();
      token.onCancellationRequested(() => abort.abort());

      const messages: ChatMessage[] = buildMessages(chatContext.history, question);

      try {
        for (let turn = 0; turn < MAX_AGENT_TURNS; turn++) {
          if (token.isCancellationRequested) break;
          if (turn > 0) {
            stream.progress("Continuing…");
          }

          const result = await runTurn(client, messages, stream, abort.signal, token);
          if (result.toolCalls.length === 0) break;

          messages.push({
            role: "assistant",
            content: result.content,
            tool_calls: result.toolCalls as unknown[],
          });

          for (const call of result.toolCalls) {
            stream.markdown(`\n\n*Running **${call.function.name}**…*\n\n`);
            const output = await executeTool(call);
            messages.push({
              role: "tool",
              tool_call_id: call.id,
              content: output,
            });
          }
        }
      } catch (error) {
        if (token.isCancellationRequested) return {};
        if (error instanceof AuthError) {
          stream.markdown(`${error.message}\n\n`);
          stream.button({ command: "knowledgeBase.signIn", title: "Sign in" });
          return { errorDetails: { message: error.message } };
        }
        const message = (error as Error).message ?? String(error);
        return { errorDetails: { message } };
      }

      return {};
    },
  );

  participant.iconPath = vscode.Uri.joinPath(_context.extensionUri, "media", "icon.svg");
  return participant;
}

function buildMessages(history: readonly unknown[], question: string): ChatMessage[] {
  const messages: ChatMessage[] = historyFor(history as Parameters<typeof historyFor>[0]);
  messages.push({ role: "user", content: collapse(question) });
  return messages;
}

async function runTurn(
  client: KnowledgeBaseClient,
  messages: ChatMessage[],
  stream: vscode.ChatResponseStream,
  signal: AbortSignal,
  token: vscode.CancellationToken,
): Promise<AgentTurn> {
  stream.progress("Thinking…");

  const aggregator = new ToolCallAggregator();
  let content = "";

  for await (const event of client.chatCompletionsStream(messages, signal, TOOLS)) {
    if (token.isCancellationRequested) break;

    const chunk = event.data as {
      choices?: Array<{
        delta?: { content?: string; tool_calls?: Array<Partial<ToolCall>> };
        finish_reason?: string;
      }>;
    };
    const choice = chunk.choices?.[0];
    const delta = choice?.delta;

    if (delta?.content) {
      content += delta.content;
      stream.markdown(delta.content);
    }

    if (delta?.tool_calls) {
      for (const call of delta.tool_calls) {
        aggregator.feed(call);
      }
    }
  }

  return { content, toolCalls: aggregator.finalize() };
}

class ToolCallAggregator {
  private calls = new Map<number, { id?: string; type?: string; name?: string; args: string }>();

  feed(delta: Partial<ToolCall> & { index?: number }): void {
    const index = delta.index ?? 0;
    let call = this.calls.get(index);
    if (!call) {
      call = { args: "" };
      this.calls.set(index, call);
    }
    if (delta.id) call.id = delta.id;
    if (delta.type) call.type = delta.type;
    if (delta.function?.name) call.name = delta.function.name;
    if (delta.function?.arguments) call.args += delta.function.arguments;
  }

  finalize(): ToolCall[] {
    return Array.from(this.calls.values())
      .filter((call): call is { id: string; type: "function"; name: string; args: string } =>
        Boolean(call.id && call.type && call.name),
      )
      .map((call) => ({
        id: call.id,
        type: call.type,
        function: { name: call.name, arguments: call.args },
      }));
  }
}
