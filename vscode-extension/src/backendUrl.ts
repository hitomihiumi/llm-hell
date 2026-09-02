/**
 * Switching which backend the extension talks to, without a trip through
 * `settings.json`.
 *
 * `baseUrl` is read on every request rather than captured at construction,
 * so a change takes effect on the very next call - there is no reconnect to
 * perform and nothing to restart. What there *is* to do is drop the session:
 * cookies are held in one map with no host attached to them, so a session
 * established against one backend would otherwise be presented to the next
 * one. That belongs to the caller; this file only decides what a typed URL
 * means and what the list of recent ones looks like afterwards.
 *
 * Pure and vscode-free, for the usual reason - the parsing is the part worth
 * testing without an extension host.
 */

/** How many previously-used backends to offer. Enough for prod, staging and a local one. */
export const RECENT_LIMIT = 6;

/**
 * A typed backend URL, as it should be stored - or nothing when it is not a
 * URL at all.
 *
 * `localhost:8000` is what somebody means rather than an error, matching
 * `withScheme` in `http.ts`, and a trailing slash is dropped so that two
 * spellings of the same backend do not become two entries in the recents.
 */
export function normaliseBackendUrl(input: string): string | undefined {
  const trimmed = input.trim();
  if (!trimmed) return undefined;
  // The scheme is decided on the untouched text: trimming trailing slashes
  // first turns a bare `http://` into `http:`, which then looks schemeless
  // and gets another one prefixed onto it.
  const withScheme = /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return undefined;
  }
  if (!parsed.hostname) return undefined;
  // A path, query or fragment is a sign somebody pasted a page rather than an
  // origin - the API routes are appended to this, so anything after the port
  // would corrupt every request instead of failing once, visibly. Refused
  // rather than trimmed away: silently dropping half of what was typed hides
  // the mistake instead of reporting it.
  if (parsed.pathname.replace(/\/+$/, "") || parsed.search || parsed.hash) return undefined;
  return parsed.origin;
}

/** `llmhell.borzo.ai` or `localhost:8000` - what to show when the whole URL is more than is wanted. */
export function describeBackend(url: string): string {
  try {
    return new URL(/^https?:\/\//i.test(url) ? url : `http://${url}`).host || url;
  } catch {
    return url;
  }
}

/**
 * The recents list after using `url`: most recent first, no duplicates, capped.
 *
 * Kept in order of use rather than of first sight, because switching between
 * two backends is the case this exists for - the one you just left should be
 * the next one offered.
 */
export function rememberBackend(
  recent: readonly string[],
  url: string,
  limit = RECENT_LIMIT,
): string[] {
  return [url, ...recent.filter((entry) => entry !== url)].slice(0, limit);
}
