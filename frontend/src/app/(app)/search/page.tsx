"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnswerPanel } from "@/components/AnswerPanel";
import { ResultCard } from "@/components/ResultCard";
import { SourceBadge } from "@/components/SourceBadge";
import { api } from "@/lib/api";
import { streamSearch } from "@/lib/sse";
import type { Citation, SearchHit, Source, SourceStatus } from "@/lib/types";

interface AnswerState {
  text: string;
  reasoning: string;
  citations: Citation[];
  model: string | null;
  stats: { hits_used?: number; hits_dropped?: number } | null;
}

const EMPTY_ANSWER: AnswerState = {
  text: "",
  reasoning: "",
  citations: [],
  model: null,
  stats: null,
};

export default function SearchPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [withAnswer, setWithAnswer] = useState(true);

  const [hits, setHits] = useState<SearchHit[]>([]);
  const [status, setStatus] = useState<SourceStatus[]>([]);
  const [answer, setAnswer] = useState<AnswerState>(EMPTY_ANSWER);
  const [searching, setSearching] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    api
      .get<Source[]>("/api/sources")
      .then((loaded) => {
        setSources(loaded);
        setSelected(new Set(loaded.filter((s) => s.enabled).map((s) => s.key)));
      })
      .catch(() => setError("Could not load the source list."));
  }, []);

  const toggle = useCallback((key: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  async function onSubmit(formEvent: React.FormEvent) {
    formEvent.preventDefault();
    if (!query.trim()) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setSearching(true);
    setStreaming(withAnswer);
    setError(null);
    setHits([]);
    setStatus([]);
    setAnswer(EMPTY_ANSWER);
    setSearched(true);

    const body = {
      query: query.trim(),
      sources: selected.size && selected.size !== sources.length ? [...selected] : null,
      answer: withAnswer,
    };

    try {
      for await (const event of streamSearch(body, controller.signal)) {
        switch (event.event) {
          case "meta":
            setAnswer((current) => ({ ...current, model: event.data.answer_model }));
            break;
          case "hits":
            // Arrives before the model is called, which is the point: the
            // results paint while the answer is still being written.
            setHits(event.data.hits);
            setStatus(event.data.source_status);
            setSearching(false);
            break;
          case "reasoning":
            setAnswer((c) => ({ ...c, reasoning: c.reasoning + event.data.text }));
            break;
          case "token":
            setAnswer((c) => ({ ...c, text: c.text + event.data.text }));
            break;
          case "citations":
            setAnswer((c) => ({ ...c, citations: event.data.citations }));
            break;
          case "done":
            setAnswer((c) => ({
              ...c,
              stats: {
                hits_used: event.data.hits_used,
                hits_dropped: event.data.hits_dropped,
              },
            }));
            setStreaming(false);
            break;
          case "error":
            setError(event.data.message);
            setStreaming(false);
            break;
        }
      }
    } catch (caught) {
      if (!controller.signal.aborted) setError("The search failed.");
    } finally {
      setSearching(false);
      setStreaming(false);
    }
  }

  const failing = status.filter((entry) => !entry.ok);

  return (
    <div className="space-y-6">
      <form onSubmit={onSubmit} className="space-y-3">
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(changeEvent) => setQuery(changeEvent.target.value)}
            placeholder="Ask a question, or search for a term…"
            autoFocus
            className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
          />
          <button
            type="submit"
            disabled={searching || !query.trim()}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {searching ? "Searching…" : "Search"}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {sources.map((source) => {
            const on = selected.has(source.key);
            return (
              <button
                key={source.key}
                type="button"
                onClick={() => toggle(source.key)}
                disabled={!source.enabled}
                title={source.enabled ? undefined : "This source is switched off by an admin"}
                className={`rounded-full border px-3 py-1 text-xs transition-colors disabled:opacity-40 ${
                  on
                    ? "border-accent bg-accent-soft text-accent"
                    : "border-border bg-surface text-muted"
                }`}
              >
                {source.display_name}
              </button>
            );
          })}

          <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              checked={withAnswer}
              onChange={(changeEvent) => setWithAnswer(changeEvent.target.checked)}
            />
            Generate an answer
          </label>
        </div>
      </form>

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {status.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {status.map((entry) => (
            <SourceBadge key={entry.source} status={entry} />
          ))}
        </div>
      )}

      {withAnswer && (
        <AnswerPanel
          text={answer.text}
          reasoning={answer.reasoning}
          citations={answer.citations}
          streaming={streaming}
          model={answer.model}
          stats={answer.stats}
        />
      )}

      {hits.length > 0 ? (
        <ol className="space-y-3">
          {hits.map((hit, index) => (
            <ResultCard key={hit.id} hit={hit} index={index + 1} />
          ))}
        </ol>
      ) : (
        searched &&
        !searching && (
          <p className="text-sm text-muted">
            No results.
            {failing.length > 0 && (
              <>
                {" "}
                {failing.length} of {status.length} sources could not be reached — the
                badges above say why.
              </>
            )}
          </p>
        )
      )}
    </div>
  );
}
