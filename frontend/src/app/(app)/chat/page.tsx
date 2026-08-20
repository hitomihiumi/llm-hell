"use client";

import { useEffect, useRef, useState } from "react";
import { ChatMessage } from "@/components/ChatMessage";
import { SpaceButton } from "@/components/SpaceButton";
import { useCopy } from "@/i18n/LocaleProvider";
import { api } from "@/lib/api";
import type { ChatTurn, Source } from "@/lib/types";
import { type SearchRun, useSearchRun } from "@/lib/useSearch";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { copy } = useCopy();
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
    <div className="flex min-h-[calc(100vh-11rem)] flex-col">
      {empty ? (
        <div className="relative flex flex-1 flex-col items-start justify-center py-16">
          <div
            aria-hidden="true"
            className="media-grid absolute inset-0 -z-10"
          />

          <p className="font-display text-[11px] uppercase tracking-[0.42em] text-white/40">
            {copy.chat.eyebrow}
          </p>
          <h1 className="mt-3 max-w-3xl font-display text-4xl font-semibold uppercase leading-[0.98] tracking-tight text-white sm:text-6xl">
            {copy.chat.title}
          </h1>
          <p className="mt-5 max-w-md text-sm leading-relaxed text-white/55">
            {copy.chat.lede}
          </p>

          <div className="mt-10 flex w-full max-w-2xl flex-col border-t border-hairline">
            {copy.chat.suggestions.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => send(suggestion)}
                className="group flex items-center justify-between gap-6 border-b border-hairline py-4 text-left transition-colors duration-300 hover:bg-white/[0.02]"
              >
                <span className="text-sm text-white/70 transition-transform duration-500 ease-out-expo group-hover:translate-x-1.5 group-hover:text-white">
                  {suggestion}
                </span>
                <svg
                  viewBox="0 0 24 12"
                  aria-hidden="true"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.25"
                  className="h-3 w-6 shrink-0 text-white/30 transition-all duration-500 ease-out-expo group-hover:translate-x-1.5 group-hover:text-white"
                >
                  <path d="M0 6h21M17 1.5 21.5 6 17 10.5" />
                </svg>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="flex-1 space-y-10 pb-8">
          {runs.map((run, index) => (
            <ChatMessage key={`${index}-${run.query}`} run={run} />
          ))}
          <div ref={bottomRef} />
        </div>
      )}

      {/* Composer, pinned to the bottom of the column. */}
      <div className="sticky bottom-0 -mx-6 border-t border-hairline bg-black/90 px-6 pt-4 pb-6 backdrop-blur-md md:-mx-10 md:px-10">
        <textarea
          value={input}
          onChange={(changeEvent) => setInput(changeEvent.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          placeholder={copy.chat.placeholder}
          className="w-full resize-none border-b border-hairline bg-transparent py-3 text-[15px] text-white outline-none transition-colors duration-300 placeholder:text-white/25 focus:border-white"
        />

        <div className="mt-3 flex items-center gap-4">
          <button
            type="button"
            onClick={() => setShowFilters(!showFilters)}
            className="font-display text-[10px] uppercase tracking-[0.24em] text-white/40 transition-colors duration-300 hover:text-white"
          >
            {selected.size === sources.length
              ? copy.chat.allSources
              : copy.chat.someSources(selected.size, sources.length)}
          </button>

          <div className="ml-auto flex items-center gap-3">
            {busy && (
              <SpaceButton
                variant="ghost"
                size="sm"
                withArrow={false}
                onClick={() => {
                  cancel();
                  setBusy(false);
                }}
                className="border-hairline text-white/60"
              >
                {copy.chat.stop}
              </SpaceButton>
            )}
            <SpaceButton
              variant="solid"
              size="sm"
              onClick={() => send(input)}
              disabled={busy || !input.trim()}
            >
              {copy.chat.send}
            </SpaceButton>
          </div>
        </div>

        {showFilters && (
          <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-hairline pt-4">
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
                  className={cn(
                    "flex items-center gap-2 font-display text-[11px] uppercase tracking-[0.24em] transition-colors duration-300 disabled:opacity-30",
                    on ? "text-white" : "text-white/40 hover:text-white/70",
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "h-1.5 w-1.5 border transition-colors duration-300",
                      on
                        ? "border-accent bg-accent"
                        : "border-white/30 bg-transparent",
                    )}
                  />
                  {source.display_name}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
