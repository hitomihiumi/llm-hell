"use client";

import { useState } from "react";
import type { SourceStatus } from "@/lib/types";

/**
 * Per-source outcome. Failures are shown, not hidden: a search that quietly
 * returns fewer results because a backend is down is worse than one that
 * says GitLab is unreachable, because the user has no reason to distrust it.
 */
export function SourceBadge({ status }: { status: SourceStatus }) {
  const [open, setOpen] = useState(false);
  const sql = typeof status.detail?.sql === "string" ? status.detail.sql : null;
  const mode =
    typeof status.detail?.mode === "string" ? status.detail.mode : null;
  const expandable = Boolean(status.error || sql);

  const tone = !status.ok
    ? "border-danger bg-danger-soft text-danger"
    : status.degraded
      ? "border-border bg-surface text-foreground"
      : "border-border bg-surface text-muted";

  return (
    <div className={`rounded-md border px-2.5 py-1.5 text-xs ${tone}`}>
      <button
        type="button"
        onClick={() => expandable && setOpen(!open)}
        className={`flex items-center gap-2 ${expandable ? "cursor-pointer" : "cursor-default"}`}
      >
        <span className="font-medium">
          {status.display_name || status.source}
        </span>
        {status.ok ? (
          <span>
            {status.hits} {status.hits === 1 ? "hit" : "hits"} ·{" "}
            {status.elapsed_ms}ms
          </span>
        ) : (
          <span>unavailable</span>
        )}
        {status.degraded && (
          <span title="some parts of this source failed">partial</span>
        )}
        {mode === "fallback" && (
          <span
            className="rounded bg-accent-soft px-1 text-accent"
            title="The model did not produce usable SQL, so a deterministic keyword query was used instead."
          >
            fallback
          </span>
        )}
        {expandable && <span aria-hidden>{open ? "▴" : "▾"}</span>}
      </button>

      {open && (
        <div className="mt-2 space-y-2 border-t border-border pt-2">
          {status.error && (
            <p className="font-mono break-words">{status.error}</p>
          )}
          {sql && (
            <div>
              <p className="mb-1 text-muted">
                Generated SQL{mode ? ` (${mode})` : ""}:
              </p>
              <pre className="overflow-x-auto rounded bg-background p-2 font-mono text-[11px] whitespace-pre-wrap">
                {sql}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
