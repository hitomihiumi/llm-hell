"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

/**
 * Two ways to look at the same pipeline.
 *
 * Search is the one that shows the machinery — per-source timings, the
 * generated SQL, the whole ranked list. Chat is the familiar shape and is
 * better for follow-up questions, at the cost of pushing the mechanics
 * behind a disclosure.
 *
 * Styled as the site styles its nav: no pill, no filled tab — just wide
 * uppercase lettering with a rule that draws in underneath, which is
 * permanent for the active one.
 */
const OPTIONS = [
  { href: "/search", label: "Search" },
  { href: "/chat", label: "Chat" },
];

export function LayoutSwitch() {
  const pathname = usePathname();

  return (
    <div role="tablist" aria-label="Layout" className="flex items-center gap-7">
      {OPTIONS.map((option) => {
        const active = pathname === option.href;
        return (
          <Link
            key={option.href}
            href={option.href}
            role="tab"
            aria-selected={active}
            className={cn(
              "group relative font-display text-[11px] uppercase tracking-[0.24em] transition-colors duration-300",
              "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white",
              active ? "text-white" : "text-white/50 hover:text-white",
            )}
          >
            {option.label}
            <span
              aria-hidden="true"
              className={cn(
                "absolute -bottom-2 left-0 h-px w-full origin-left bg-white transition-transform duration-500 ease-out-expo",
                active ? "scale-x-100" : "scale-x-0 group-hover:scale-x-100",
              )}
            />
          </Link>
        );
      })}
    </div>
  );
}
