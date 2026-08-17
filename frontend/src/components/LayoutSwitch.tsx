"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Two ways to look at the same pipeline.
 *
 * Search is the one that shows the machinery — per-source timings, the
 * generated SQL, the whole ranked list. Chat is the familiar shape and is
 * better for follow-up questions, at the cost of pushing the mechanics
 * behind a disclosure.
 */
const OPTIONS = [
  { href: "/search", label: "Search" },
  { href: "/chat", label: "Chat" },
];

export function LayoutSwitch() {
  const pathname = usePathname();

  return (
    <div
      role="tablist"
      aria-label="Layout"
      className="flex items-center gap-0.5 rounded-md border border-border bg-surface p-0.5"
    >
      {OPTIONS.map((option) => {
        const active = pathname === option.href;
        return (
          <Link
            key={option.href}
            href={option.href}
            role="tab"
            aria-selected={active}
            className={`rounded px-2.5 py-1 text-xs transition-colors ${
              active
                ? "bg-accent-soft text-accent"
                : "text-muted hover:text-foreground"
            }`}
          >
            {option.label}
          </Link>
        );
      })}
    </div>
  );
}
