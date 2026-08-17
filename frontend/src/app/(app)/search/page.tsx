"use client";

import { useCallback, useEffect, useState } from "react";
import { AnswerPanel } from "@/components/AnswerPanel";
import { ResultCard } from "@/components/ResultCard";
import { SourceBadge } from "@/components/SourceBadge";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";
import { useSearchRun } from "@/lib/useSearch";

/**
 * The layout that shows the machinery: per-source timings, the generated SQL,
 * the whole ranked list. `/chat` is the same pipeline in a conversational
 * shape; both share the SSE handling in useSearchRun.
 */
export default function SearchPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [withAnswer, setWithAnswer] = useState(true);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const { run, start } = useSearchRun();

  useEffect(() => {
    api
      .get<Source[]>("/api/sources")
      .then((loaded) => {
        setSources(loaded);
        setSelected(new Set(loaded.filter((s) => s.enabled).map((s) => s.key)));
      })
      .catch(() => setSourcesError("Could not load the source list."));
  }, []);

  const toggle = useCallback((key: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  function onSubmit(formEvent: React.FormEvent) {
    formEvent.preventDefault();
    if (!query.trim()) return;
    start(query.trim(), {
      sources:
        selected.size && selected.size !== sources.length
          ? [...selected]
          : null,
      answer: withAnswer,
    });
  }

  const error = sourcesError ?? run?.error ?? null;
  const failing = run?.status.filter((entry) => !entry.ok) ?? [];

  return (
    <div className="space-y-6">
      <form onSubmit={onSubmit} className="space-y-3">
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(changeEvent) => setQuery(changeEvent.target.value)}
            placeholder="Ask a question, or search for a term…"
            // biome-ignore lint/a11y/noAutofocus: the sole input on a single-purpose screen; focusing it is what every user wants first
            autoFocus
            className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
          />
          <button
            type="submit"
            disabled={run?.searching || !query.trim()}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {run?.searching ? "Searching…" : "Search"}
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
                title={
                  source.enabled
                    ? undefined
                    : "This source is switched off by an admin"
                }
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
              onChange={(changeEvent) =>
                setWithAnswer(changeEvent.target.checked)
              }
            />
            Generate an answer
          </label>
        </div>
      </form>

      {error && (
        <p
          role="alert"
          className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
        >
          {error}
        </p>
      )}

      {run && run.status.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {run.status.map((entry) => (
            <SourceBadge key={entry.source} status={entry} />
          ))}
        </div>
      )}

      {run && withAnswer && (
        <AnswerPanel
          text={run.answer}
          reasoning={run.reasoning}
          citations={run.citations}
          streaming={run.streaming}
          model={run.model}
          stats={
            run.hitsUsed === undefined
              ? null
              : { hits_used: run.hitsUsed, hits_dropped: run.hitsDropped }
          }
        />
      )}

      {run && run.hits.length > 0 ? (
        <ol className="space-y-3">
          {run.hits.map((hit, index) => (
            <ResultCard key={hit.id} hit={hit} index={index + 1} />
          ))}
        </ol>
      ) : (
        run &&
        !run.searching && (
          <p className="text-sm text-muted">
            No results.
            {failing.length > 0 && (
              <>
                {" "}
                {failing.length} of {run.status.length} sources could not be
                reached — the badges above say why.
              </>
            )}
          </p>
        )
      )}
    </div>
  );
}
