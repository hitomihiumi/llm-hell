"use client";

import { useEffect, useRef, useState } from "react";
import { ChatMessage } from "@/components/ChatMessage";
import { api } from "@/lib/api";
import type { ChatTurn, Source } from "@/lib/types";
import { type SearchRun, useSearchRun } from "@/lib/useSearch";

const SUGGESTIONS = [
  "How does result ranking work?",
  "Why must the KV cache be fp8?",
  "What breaks when the open-file limit is too low?",
];

export default function ChatPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showFilters, setShowFilters] = useState(false);
  const [input, setInput] = useState("");
  const [runs, setRuns] = useState<SearchRun[]>([]);
  const [busy, setBusy] = useState(false);

  const { start, cancel } = useSearchRun();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .get<Source[]>("/api/sources")
      .then((loaded) => {
        setSources(loaded);
        setSelected(new Set(loaded.filter((s) => s.enabled).map((s) => s.key)));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    // Reading runs.length rather than depending on `runs` purely as a
    // trigger - and it is the right behaviour too: there is nothing to
    // scroll to on an empty transcript.
    if (runs.length === 0) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [runs.length]);

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    setInput("");
    setBusy(true);

    // Prior turns, so a follow-up like "what about the second one" has
    // something to refer back to. The search itself still runs on the new
    // question alone - see the note on SearchRequest.history in the backend.
    const history: ChatTurn[] = runs.flatMap((run) =>
      run.answer
        ? [
            { role: "user" as const, content: run.query },
            { role: "assistant" as const, content: run.answer },
          ]
        : [],
    );

    const index = runs.length;
    await start(
      trimmed,
      {
        sources:
          selected.size && selected.size !== sources.length
            ? [...selected]
            : null,
        history,
      },
      // Replace this turn in place on every event, so the answer streams
      // into the transcript rather than appearing at the end.
      (live) =>
        setRuns((current) => {
          const next = [...current];
          next[index] = live;
          return next;
        }),
    );

    setBusy(false);
  }

  function onKeyDown(keyEvent: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter makes a newline - the convention everywhere
    // else, and getting it wrong is immediately annoying.
    if (keyEvent.key === "Enter" && !keyEvent.shiftKey) {
      keyEvent.preventDefault();
      send(input);
    }
  }

  const empty = runs.length === 0;

  return (
    <div className="flex min-h-[calc(100vh-9rem)] flex-col">
      {empty ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-6 text-center">
          <div className="space-y-1.5">
            <h1 className="text-xl font-semibold tracking-tight">
              Ask the knowledge base
            </h1>
            <p className="text-sm text-muted">
              Answers come from your Drive, GitLab and internal records — with
              the sources attached.
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => send(suggestion)}
                className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted hover:border-accent hover:text-foreground"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="flex-1 space-y-8 pb-6">
          {runs.map((run, index) => (
            <ChatMessage key={`${index}-${run.query}`} run={run} />
          ))}
          <div ref={bottomRef} />
        </div>
      )}

      {/* Composer, pinned to the bottom of the column. */}
      <div className="sticky bottom-0 -mx-6 bg-background px-6 pt-3 pb-6">
        <div className="rounded-2xl border border-border bg-surface p-2">
          <textarea
            value={input}
            onChange={(changeEvent) => setInput(changeEvent.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"
            className="w-full resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted"
          />
          <div className="flex items-center gap-2 px-1">
            <button
              type="button"
              onClick={() => setShowFilters(!showFilters)}
              className="rounded-md px-2 py-1 text-xs text-muted hover:text-foreground"
            >
              {selected.size === sources.length
                ? "All sources"
                : `${selected.size} of ${sources.length} sources`}
            </button>

            <div className="ml-auto flex items-center gap-2">
              {busy && (
                <button
                  type="button"
                  onClick={() => {
                    cancel();
                    setBusy(false);
                  }}
                  className="rounded-md border border-border px-2.5 py-1 text-xs text-muted hover:text-foreground"
                >
                  Stop
                </button>
              )}
              <button
                type="button"
                onClick={() => send(input)}
                disabled={busy || !input.trim()}
                className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                Send
              </button>
            </div>
          </div>

          {showFilters && (
            <div className="flex flex-wrap gap-2 border-t border-border px-1 pt-2">
              {sources.map((source) => {
                const on = selected.has(source.key);
                return (
                  <button
                    key={source.key}
                    type="button"
                    disabled={!source.enabled}
                    title={
                      source.enabled ? undefined : "Switched off by an admin"
                    }
                    onClick={() =>
                      setSelected((current) => {
                        const next = new Set(current);
                        if (next.has(source.key)) next.delete(source.key);
                        else next.add(source.key);
                        return next;
                      })
                    }
                    className={`rounded-full border px-2.5 py-1 text-xs disabled:opacity-40 ${
                      on
                        ? "border-accent bg-accent-soft text-accent"
                        : "border-border text-muted"
                    }`}
                  >
                    {source.display_name}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
