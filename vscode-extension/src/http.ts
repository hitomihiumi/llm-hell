/**
 * The two HTTP details that are easy to get quietly wrong.
 *
 * Both are here rather than in `client.ts` because they are pure, and pure is
 * what can be tested outside an extension host — `client.ts` cannot even be
 * imported without the editor's API.
 */

/**
 * `Set-Cookie` headers, which are the one header that may legitimately repeat.
 *
 * `headers.get("set-cookie")` folds them into a single comma-joined string,
 * and a cookie's own `Expires` attribute contains a comma — so splitting that
 * string is a trap. `getSetCookie()` returns them properly separated; the
 * fallback exists only for a runtime old enough not to have it.
 */
export function readSetCookie(response: {
  headers: { getSetCookie?: () => string[]; get: (name: string) => string | null };
}): string[] {
  const all = response.headers.getSetCookie?.();
  if (all) return all;
  const single = response.headers.get("set-cookie");
  return single ? [single] : [];
}

/** name -> value, for the cookies worth keeping. */
export function parseCookies(lines: string[], keep: readonly string[]): Map<string, string> {
  const cookies = new Map<string, string>();
  for (const line of lines) {
    const [pair] = line.split(";");
    const separator = pair.indexOf("=");
    if (separator <= 0) continue;
    const name = pair.slice(0, separator).trim();
    if (!keep.includes(name)) continue;
    cookies.set(name, pair.slice(separator + 1).trim());
  }
  return cookies;
}

/** A base URL typed as `localhost:8000` is what someone means, not an error. */
export function withScheme(baseUrl: string): string {
  const trimmed = baseUrl.trim().replace(/\/+$/, "");
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}
