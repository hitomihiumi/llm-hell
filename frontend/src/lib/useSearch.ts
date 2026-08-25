"use client";

import { useCallback, useRef, useState } from "react";
import { sanitizeAnswerText } from "@/lib/sanitizeAnswer";
import { streamSearch } from "@/lib/sse";
import type { ChatTurn, Citation, SearchHit, SourceStatus } from "@/lib/types";

export interface SearchRun {
  query: string;
  hits: SearchHit[];
  status: SourceStatus[];
  answer: string;
  reasoning: string;
  citations: Citation[];
  model: string | null;
  hitsUsed?: number;
  hitsDropped?: number;
  citedHitIds: string[];
  /** Fan-out finished; the result list can be painted. */
  searching: boolean;
  /** The answer is still being written. */
  streaming: boolean;
  error: string | null;
}

export function emptyRun(query: string): SearchRun {
  return {
    query,
    hits: [],
    status: [],
    answer: "",
    reasoning: "",
    citations: [],
    citedHitIds: [],
    model: null,
    searching: true,
    streaming: false,
    error: null,
  };
}

export interface RunOptions {
  sources?: string[] | null;
  answer?: boolean;
  history?: ChatTurn[];
}

/**
 * Runs one streamed search and folds its events into a `SearchRun`.
 *
 * Shared by both layouts so the event handling exists once: the search page
 * shows a single run, the chat page keeps a list of them, and neither needs
 * its own copy of the SSE state machine.
 */
export function useSearchRun() {
  const [run, setRun] = useState<SearchRun | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const start = useCallback(
    async (
      query: string,
      options: RunOptions = {},
      onRun?: (run: SearchRun) => void,
    ): Promise<SearchRun> => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      let current = emptyRun(query);
      current.streaming = options.answer !== false;

      const publish = (next: Partial<SearchRun>) => {
        current = { ...current, ...next };
        setRun(current);
        onRun?.(current);
      };
      publish({});

      try {
        for await (const event of streamSearch(
          {
            query,
            sources: options.sources ?? null,
            answer: options.answer ?? true,
            history: options.history ?? null,
          },
          controller.signal,
        )) {
          switch (event.event) {
            case "meta":
              publish({ model: event.data.answer_model });
              break;
            case "hits":
              // Before the model is called - this is what lets results
              // paint while the answer is still being written.
              publish({
                hits: event.data.hits,
                status: event.data.source_status,
                searching: false,
              });
              break;
            case "reasoning":
              publish({
                reasoning: sanitizeAnswerText(
                  current.reasoning + event.data.text,
                ),
              });
              break;
            case "token":
              publish({
                answer: sanitizeAnswerText(current.answer + event.data.text),
              });
              break;
            case "citations":
              publish({ citations: event.data.citations });
              break;
            case "done":
              publish({
                streaming: false,
                searching: false,
                hitsUsed: event.data.hits_used,
                hitsDropped: event.data.hits_dropped,
                citedHitIds: event.data.cited_hit_ids ?? current.citedHitIds,
              });
              break;
            case "error":
              publish({
                error: event.data.message,
                streaming: false,
                searching: false,
              });
              break;
          }
        }
      } catch {
        if (!controller.signal.aborted) {
          publish({
            error: "The search failed.",
            streaming: false,
            searching: false,
          });
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        if (current.searching || current.streaming) {
          publish({ searching: false, streaming: false });
        }
      }

      return current;
    },
    [],
  );

  return { run, start, cancel, setRun };
}
