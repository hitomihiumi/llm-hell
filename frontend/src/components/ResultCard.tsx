"use client";

import Link from "next/link";
import type { SearchHit } from "@/lib/types";

const KIND_LABEL: Record<string, string> = {
  document: "Doc",
  email: "Email",
  code: "Code",
  repository: "Repo",
  commit: "Commit",
  row: "Record",
  unknown: "Item",
};

function formatDate(value: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString();
}

export function ResultCard({ hit, index }: { hit: SearchHit; index: number }) {
  const date = formatDate(hit.timestamp);
  // Internal record links are relative and route inside the app; everything
  // else is an external permalink and opens in a new tab.
  const internal = hit.url?.startsWith("/") ?? false;

  return (
    <li
      id={`hit-${hit.id}`}
      className="scroll-mt-24 rounded-lg border border-border bg-surface p-4 transition-colors"
    >
      <div className="flex items-baseline gap-2">
        <span className="text-xs text-muted tabular-nums">[{index}]</span>
        {hit.url ? (
          internal ? (
            <Link
              href={hit.url}
              className="font-medium text-accent hover:underline"
            >
              {hit.title}
            </Link>
          ) : (
            <a
              href={hit.url}
              target="_blank"
              rel="noreferrer noopener"
              className="font-medium text-accent hover:underline"
            >
              {hit.title}
            </a>
          )
        ) : (
          // No link rather than a dead one: some sources genuinely have no
          // addressable location for a hit.
          <span className="font-medium">{hit.title}</span>
        )}
      </div>

      {hit.snippet && (
        <p className="mt-1.5 text-sm text-muted">{hit.snippet}</p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="rounded bg-background px-1.5 py-0.5">
          {KIND_LABEL[hit.kind] ?? hit.kind}
        </span>
        <span>{hit.source}</span>
        {hit.author && <span>{hit.author}</span>}
        {date && <span>{date}</span>}
      </div>
    </li>
  );
}
