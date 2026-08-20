import type { Locale } from "./config";

/**
 * Ukrainian has three plural forms where English has two, so a dictionary
 * entry is a list and the locale decides how to read it.
 *
 * Doing this by concatenating an "s" — or by picking one Ukrainian form and
 * living with it — produces "1 результатів" and "5 результат", which is the
 * kind of thing that makes a translated interface read as machine output.
 *
 *   en: [one, other]              1 hit,  2 hits
 *   uk: [one, few, many]          1 хіт,  2 хіти,  5 хітів
 */
export function pluralise(
  locale: Locale,
  count: number,
  forms: string[],
): string {
  if (locale !== "uk") {
    return forms[count === 1 ? 0 : 1] ?? forms[forms.length - 1];
  }

  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;

  // 11-14 are the exception: they end in 1-4 but take the "many" form.
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2] ?? forms[forms.length - 1];
}
