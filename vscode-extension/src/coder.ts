import * as vscode from "vscode";
import { environmentMessage } from "./agentContext";
import type { AgentMode } from "./agentMode";
import { AuthError, type ChatMessage, type KnowledgeBaseClient } from "./client";
import { collapse, historyFor } from "./format";
import { ToolCallAggregator } from "./toolCallAggregator";
import { executeTool, TOOLS, type ToolCall } from "./tools";

/**
 * `@coder` in the chat panel.
 *
 * Where `@kb` searches the corpus and cites what it found, `@coder` is an
 * agent: the backend hands the conversation to a tool-calling model — DeepSeek
 * by default, pinned with `AGENT_MODEL_ID` — and this participant runs
 * whatever that model asks for.
 *
 * The tools run in the extension host, because that is where the files and the
 * terminal are. `search_knowledge_base` runs here too and reaches the backend
 * from here: keeping every tool on one side of the wire means the loop has one
 * shape rather than two.
 *
 * That loop is the participant's whole job. Send the conversation, collect the
 * `tool_calls` the model streams back, execute them, append the results, and
 * go round again until it answers with prose instead of another call.
 */

export const PARTICIPANT_ID = "knowledgeBase.coder";

interface AgentTurn {
  content: string;
  toolCalls: ToolCall[];
}

export function registerCoderParticipant(
  _context: vscode.ExtensionContext,
  client: KnowledgeBaseClient,
  maxAgentTurns: () => number,
  agentMode: () => AgentMode,
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

      const messages: ChatMessage[] = [
        environmentMessage(),
        ...buildMessages(chatContext.history, question),
      ];

      const turnLimit = Math.max(1, Math.min(100, maxAgentTurns()));
      let finished = false;
      try {
        for (let turn = 0; turn < turnLimit; turn++) {
          if (token.isCancellationRequested) break;
          if (turn > 0) {
            stream.progress("Continuing…");
          }

          const result = await runTurn(client, messages, stream, abort.signal, token);
          if (result.toolCalls.length === 0) {
            finished = true;
            break;
          }

          messages.push({
            role: "assistant",
            content: result.content,
            tool_calls: result.toolCalls as unknown[],
          });

          for (const call of result.toolCalls) {
            stream.markdown(`\n\n*Running **${call.function.name}**…*\n\n`);
            const output = await executeTool(call, client, agentMode());
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

      if (!finished && !token.isCancellationRequested) {
        // The loop ran out of turns rather than reaching an answer. Saying so
        // is the difference between an agent that stopped and an agent that
        // looks like it forgot what it was doing halfway through.
        stream.markdown(`\n\n_Stopped after ${turnLimit} tool rounds. Ask again to carry on._\n`);
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
