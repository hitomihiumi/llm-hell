"use client";

import { usePathname } from "next/navigation";
import { useTransition } from "react";
import { setLocale } from "@/i18n/actions";
import type { Locale } from "@/i18n/config";
import { cn } from "@/lib/utils";

const OPTIONS: { locale: Locale; label: string }[] = [
  { locale: "en", label: "EN" },
  { locale: "uk", label: "UA" },
];

/**
 * EN / UA toggle. Persists the choice as a cookie and returns to the current
 * path — the same component the site uses, kept identical on purpose so the
 * demo and the site behave the same way in front of a visitor.
 */
export function LocaleSwitcher({
  locale,
  className,
}: {
  locale: Locale;
  className?: string;
}) {
  const pathname = usePathname();
  const [pending, startTransition] = useTransition();

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 font-display text-[11px] uppercase tracking-[0.2em]",
        className,
      )}
    >
      {OPTIONS.map((option, index) => (
        <span key={option.locale} className="flex items-center gap-1.5">
          {index > 0 && (
            <span aria-hidden="true" className="text-white/25">
              /
            </span>
          )}
          <button
            type="button"
            disabled={pending || option.locale === locale}
            aria-current={option.locale === locale ? "true" : undefined}
            onClick={() =>
              startTransition(async () => {
                await setLocale(option.locale, pathname);
              })
            }
            className={cn(
              "transition-colors duration-300 disabled:cursor-default",
              option.locale === locale
                ? "text-white"
                : "text-white/40 hover:text-white",
            )}
          >
            {option.label}
          </button>
        </span>
      ))}
    </div>
  );
}
