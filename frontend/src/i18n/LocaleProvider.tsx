"use client";

import { createContext, useContext, useMemo } from "react";
import { DEFAULT_LOCALE, type Locale } from "./config";
import { type Copy, DICTIONARY } from "./dictionary";
import { pluralise } from "./plural";

interface LocaleValue {
  locale: Locale;
  copy: Copy;
  /** Picks the right plural form for the active locale. */
  plural: (count: number, forms: string[]) => string;
}

const LocaleContext = createContext<LocaleValue | null>(null);

/**
 * Carries the resolved locale to the client tree.
 *
 * The site passes `locale` down as a prop, because its pages are server
 * components rendering their own copy. This app is the other shape: search,
 * chat and sources are all client components driving live requests, so
 * threading a prop through every one of them would mean touching each
 * component again for the next string. The locale is resolved once on the
 * server, in the app shell, and read from context below it.
 */
export function LocaleProvider({
  locale,
  children,
}: {
  locale: Locale;
  children: React.ReactNode;
}) {
  const value = useMemo<LocaleValue>(
    () => ({
      locale,
      copy: DICTIONARY[locale],
      plural: (count, forms) => pluralise(locale, count, forms),
    }),
    [locale],
  );

  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  );
}

/**
 * The active locale's copy.
 *
 * Falls back to the default locale rather than throwing when used outside a
 * provider: the login page renders outside the app shell, and an interface
 * that crashes because a translation is missing is worse than one that shows
 * English.
 */
export function useCopy(): LocaleValue {
  const value = useContext(LocaleContext);
  if (value) return value;
  return {
    locale: DEFAULT_LOCALE,
    copy: DICTIONARY[DEFAULT_LOCALE],
    plural: (count, forms) => pluralise(DEFAULT_LOCALE, count, forms),
  };
}
