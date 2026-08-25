"use client";

import { useState } from "react";
import { useCopy } from "@/i18n/LocaleProvider";
import type { ContainerGroup } from "@/lib/hitGroups";
import { cn } from "@/lib/utils";

/**
 * Filtering by the document, not by the provider.
 *
 * The badge row above this one answers "which systems were reachable" —
 * useful, and a different question from the one people actually ask of a
 * result list, which is "show me the ones from auth-service" or "just that
 * README". This is that filter: repositories and tables at the top level,
 * the files and rows inside them one level down, both selectable.
 *
 * A filled marker means the answer actually cited something in that row, and
 * those sort first. That distinction is the point of the whole list: a search
 * can return forty results across four repositories and the answer stand on
 * two files, and until it is shown, every result looks equally load-bearing.
 *
 * Only groups with a second level get a disclosure control. A Drive document
 * is not inside anything, so it is one row — expanding it would show its own
 * name back to it.
 */
export function DocumentFilter({
  groups,
  selected,
  onSelect,
}: {
  groups: ContainerGroup[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const { copy, plural } = useCopy();
  // Containers holding what the answer cited start open, because that is what
  // the reader came to look at. The rest stay shut so a search across a dozen
  // repositories is still a list you can see the shape of.
  const [expanded, setExpanded] = useState<Set<string>>(
    () =>
      new Set(groups.filter((group) => group.cited).map((group) => group.key)),
  );

  if (groups.length === 0) return null;

  const toggle = (key: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(key)) next.add(key);
      return next;
    });

  return (
    <div className="border border-hairline">
      <div className="flex items-center justify-between border-b border-hairline px-3 py-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/40">
          {copy.documentFilter.title}
        </span>
        {selected && (
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/40 underline decoration-white/20 underline-offset-4 transition-colors hover:text-white"
          >
            {copy.documentFilter.clear}
          </button>
        )}
      </div>

      <ul>
        {groups.map((group) => {
          const open = expanded.has(group.key);
          return (
            <li
              key={group.key}
              className="border-b border-hairline last:border-b-0"
            >
              <div className="flex items-stretch">
                {group.standalone ? (
                  <span className="w-6 flex-none" aria-hidden="true" />
                ) : (
                  <button
                    type="button"
                    onClick={() => toggle(group.key)}
                    aria-expanded={open}
                    aria-label={
                      open
                        ? copy.documentFilter.collapse(group.title)
                        : copy.documentFilter.expand(group.title)
                    }
                    className="w-6 flex-none text-white/30 transition-colors hover:text-white"
                  >
                    <span
                      className={cn(
                        "inline-block text-[9px] transition-transform",
                        open && "rotate-90",
                      )}
                    >
                      ▶
                    </span>
                  </button>
                )}
                <Row
                  title={group.title}
                  count={group.hits.length}
                  cited={group.cited}
                  selected={selected === group.key}
                  onClick={() =>
                    onSelect(selected === group.key ? null : group.key)
                  }
                  hits={plural(group.hits.length, copy.badge.hits)}
                  usedTitle={copy.documentFilter.usedTitle}
                />
              </div>

              {open && !group.standalone && (
                <ul className="pb-1">
                  {group.documents.map((document) => (
                    <li key={document.key} className="flex items-stretch">
                      <span className="w-6 flex-none" aria-hidden="true" />
                      <Row
                        indented
                        title={document.title}
                        count={document.hits.length}
                        cited={document.cited}
                        selected={selected === document.key}
                        onClick={() =>
                          onSelect(
                            selected === document.key ? null : document.key,
                          )
                        }
                        hits={plural(document.hits.length, copy.badge.hits)}
                        usedTitle={copy.documentFilter.usedTitle}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Row({
  title,
  count,
  cited,
  selected,
  onClick,
  hits,
  usedTitle,
  indented = false,
}: {
  title: string;
  count: number;
  cited: boolean;
  selected: boolean;
  onClick: () => void;
  hits: string;
  usedTitle: string;
  indented?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "flex min-w-0 flex-1 items-center gap-2 py-2 pr-3 text-left transition-colors",
        indented ? "pl-1" : "pl-0",
        selected ? "text-white" : "text-white/60 hover:text-white",
      )}
    >
      <span
        title={cited ? usedTitle : undefined}
        className={cn(
          "size-[6px] flex-none",
          cited ? "bg-accent" : "border border-white/25",
        )}
        aria-hidden="true"
      />
      <span
        className={cn(
          "min-w-0 flex-1 truncate",
          indented ? "font-mono text-[11px]" : "text-[13px]",
        )}
      >
        {title}
      </span>
      <span className="flex-none font-mono text-[10px] uppercase tracking-[0.16em] text-white/30">
        {count} {hits}
      </span>
    </button>
  );
}
