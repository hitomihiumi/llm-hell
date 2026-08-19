export const LOCALES = ["en", "uk"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";

// Distinct from the site's `borzo_locale`: this app can be served from the
// same host during a demo, and sharing the name would make one switch the
// other.
export const LOCALE_COOKIE = "llmhell_locale";

export function isLocale(value: string | undefined | null): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}
