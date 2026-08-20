import * as vscode from "vscode";
import { AuthError, type KnowledgeBaseClient } from "./client";
import { hitUri } from "./documents";
import { citationLinks, historyFor } from "./format";
import type { Citation, SearchHit, SearchResponse, SourceStatus } from "./types";

/**
 * `@kb` in the chat panel.
 *
 * This is the interface the extension is really for. A tree of results is a
 * fine thing to have and a poor way to ask a question: the backend takes the
 * conversation into account when it plans a search — "чи присутній тут
 * гіроскоп" only resolves to `F722 IMU` because the previous turn said which
 * board — and a sidebar has no conversation to give it. A chat does.
 *
 * So the participant is a thin pipe. The editor owns the transcript, the
 * follow-up UI and cancellation; the backend owns the planning, the search and
 * the answer. What happens here is turning one into the other:
 *
 *   - the panel's history becomes the `history` the query planner reads,
 *   - `hits` become references, the way Copilot shows the files it looked at,
 *   - `token` events become streamed Markdown,
 *   - `[1]` in that Markdown becomes a link to the result it came from.
 *
 * Results arrive about a second in and the answer is written on top of them,
 * because the server sends them in that order and this does not buffer.
 */

export const PARTICIPANT_ID = "knowledgeBase.chat";

interface Metadata {
  hits: number;
  sources: string[];
}

interface ChatSettings {
  sources: string[];
  limit: number;
  /**
   * Called with everything a turn found.
   *
   * The chat scrolls; the sidebar does not. Feeding one from the other means
   * the results of the question three turns back are still there to open,
   * which is the one thing a transcript is bad at.
   */
  onResults?: (response: SearchResponse) => void;
}

export function registerChatParticipant(
  context: vscode.ExtensionContext,
  client: KnowledgeBaseClient,
  settings: () => ChatSettings,
): vscode.ChatParticipant {
  const participant = vscode.chat.createChatParticipant(
    PARTICIPANT_ID,
    async (request, chatContext, stream, token): Promise<vscode.ChatResult> => {
      const question = request.prompt.trim();
      if (!question) {
        stream.markdown("Ask a question, or search for a term.");
        return {};
      }

      // `/find` is the same search with the model left out of it: the result
      // list, fast, and nothing billed. Worth having as its own command rather
      // than a setting, because it is a per-question decision.
      const wantsAnswer = request.command !== "find";
      const config = settings();

      const abort = new AbortController();
      token.onCancellationRequested(() => abort.abort());

      stream.progress(wantsAnswer ? "Searching…" : "Searching");

      let hits: SearchHit[] = [];
      let statuses: SourceStatus[] = [];
      let citations: Citation[] = [];
      let answer = "";
      let failure: string | undefined;

      try {
        for await (const event of client.searchStream(
          question,
          {
            sources: config.sources,
            limit: config.limit,
            answer: wantsAnswer,
            history: historyFor(chatContext.history),
          },
          abort.signal,
        )) {
          if (token.isCancellationRequested) break;

          switch (event.event) {
            case "hits": {
              const payload = event.data as { hits: SearchHit[]; source_status: SourceStatus[] };
              hits = payload.hits ?? [];
              statuses = payload.source_status ?? [];
              showReferences(stream, hits);
              reportSources(stream, statuses);
              if (!hits.length) {
                stream.markdown("\nNothing matched.\n");
              } else if (wantsAnswer) {
                stream.progress("Reading the results…");
              }
              break;
            }
            case "token": {
              const { text } = event.data as { text?: string };
              if (text) {
                answer += text;
                stream.markdown(text);
              }
              break;
            }
            case "citations": {
              citations = (event.data as { citations?: Citation[] }).citations ?? [];
              break;
            }
            case "error": {
              failure = (event.data as { message?: string }).message ?? "The search failed.";
              break;
            }
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

      if (failure) {
        return { errorDetails: { message: failure } };
      }

      config.onResults?.({
        query_id: "",
        query: question,
        queries: [],
        hits,
        source_status: statuses,
        answer: answer
          ? {
              text: answer,
              model: null,
              citations,
              hits_used: citations.length,
              hits_dropped: 0,
              hallucinated_citations: 0,
            }
          : null,
        duration_ms: 0,
      });

      if (hits.length && !answer && wantsAnswer) {
        // Results but no prose: the answer model is unreachable or unconfigured.
        // The list is still worth having, so this is a note rather than an error.
        stream.markdown("\n_No answer was written — the results are above._\n");
      }
      if (citations.length) {
        showSources(stream, citations);
      }

      return { metadata: { hits: hits.length, sources: [...new Set(hits.map((h) => h.source))] } };
    },
  );

  participant.iconPath = vscode.Uri.joinPath(context.extensionUri, "media", "icon.svg");
  participant.followupProvider = { provideFollowups };
  return participant;
}

/**
 * The results, as references.
 *
 * The same affordance Copilot uses for the files it read: a collapsed list at
 * the top of the turn, each entry opening what it points at. A hit with a
 * permalink points there; one without points at the `kb:` document, which
 * opens in an editor with its language applied.
 */
function showReferences(stream: vscode.ChatResponseStream, hits: SearchHit[]): void {
  for (const hit of hits) {
    stream.reference(hit.url ? vscode.Uri.parse(hit.url) : hitUri(hit));
  }
}

/** A source that failed is said out loud; one that worked is not worth a line. */
function reportSources(stream: vscode.ChatResponseStream, statuses: SourceStatus[]): void {
  const failed = statuses.filter((status) => !status.ok);
  if (!failed.length) return;
  const names = failed.map((status) => `**${status.display_name || status.source}**`).join(", ");
  stream.markdown(`\n_Searched without ${names}._\n\n`);
}

/**
 * The numbered list under the answer.
 *
 * Every citation here resolved to a result that was actually in the prompt —
 * the API drops the numbers the model invented before it sends them — so each
 * line is a link that goes somewhere real.
 */
function showSources(stream: vscode.ChatResponseStream, citations: Citation[]): void {
  stream.markdown("\n\n---\n\n");
  for (const line of citationLinks(citations)) {
    stream.markdown(`${line}\n`);
  }
}

function provideFollowups(result: vscode.ChatResult): vscode.ChatFollowup[] {
  const metadata = result.metadata as Metadata | undefined;
  if (!metadata?.hits) return [];
  return [
    { prompt: "Where exactly does it say that?", label: "Where does it say that?" },
    { prompt: "What else is in those documents about this?" },
  ];
}
