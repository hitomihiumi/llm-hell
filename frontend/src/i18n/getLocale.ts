import { cookies, headers } from "next/headers";
import { DEFAULT_LOCALE, isLocale, LOCALE_COOKIE, type Locale } from "./config";

/**
 * Server-only. There is no URL prefix for locale, so every request resolves
 * it from the cookie set by `LocaleSwitcher`, falling back to the browser's
 * `Accept-Language` on a first visit.
 *
 * Reading `cookies()` opts the caller out of static rendering, which costs
 * nothing here: every page behind the app shell already reads the session
 * cookie to check the user.
 */
export async function getLocale(): Promise<Locale> {
  const cookieLocale = (await cookies()).get(LOCALE_COOKIE)?.value;
  if (isLocale(cookieLocale)) return cookieLocale;

  const acceptLanguage = (await headers()).get("accept-language") ?? "";
  return acceptLanguage.toLowerCase().includes("uk") ? "uk" : DEFAULT_LOCALE;
}
