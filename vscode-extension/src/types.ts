/**
 * The shapes the API actually returns.
 *
 * Hand-written rather than generated, and kept deliberately small: only the
 * fields this extension reads are here, so a backend that grows a field does
 * not break the build and a field that disappears fails where it is used
 * rather than at parse time. The source of truth is
 * `backend/app/schemas/search.py`.
 */

export type HitKind = "document" | "email" | "code" | "repository" | "commit" | "row" | "unknown";

export interface SearchHit {
  id: string;
  source: string;
  kind: HitKind;
  title: string;
  snippet: string;
  url: string | null;
  author: string | null;
  timestamp: string | null;
  preview_pages: number | null;
  /** "grid" when the snippet is a spreadsheet rendered row by row. */
  snippet_format?: "text" | "grid";
  rank_in_source: number;
  score: number;
}

export interface SourceStatus {
  source: string;
  display_name: string;
  ok: boolean;
  degraded: boolean;
  hits: number;
  elapsed_ms: number;
  error: string | null;
  detail?: Record<string, unknown>;
}

export interface Citation {
  n: number;
  hit_id: string;
  title: string;
  url: string | null;
  source: string;
}

export interface Answer {
  text: string;
  model: string | null;
  citations: Citation[];
  hits_used: number;
  hits_dropped: number;
  hallucinated_citations: number;
}

export interface SearchResponse {
  query_id: string;
  query: string;
  /** Every phrasing that was run, the user's own first. */
  queries: string[];
  hits: SearchHit[];
  source_status: SourceStatus[];
  answer: Answer | null;
  duration_ms: number;
}

export interface Content {
  hit_id: string;
  title: string;
  text: string;
  /** A hint for the editor's syntax highlighting, not a promise. */
  language: string | null;
  truncated: boolean;
  preview_pages: number;
}

export interface Source {
  key: string;
  kind: string;
  display_name: string;
  enabled: boolean;
  weight: number;
}

/** One prior exchange, as `/api/search` and `/api/search/stream` want it. */
export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

/**
 * One provider's connection state, as the backend reports it.
 *
 * There is no field for a token, and there is no route that would return
 * one: the secret is encrypted in a column the API never reads back out. What
 * is here is what an interface needs to say whether a source will work.
 */
export interface CredentialStatus {
  provider: "google" | "gitlab";
  connected: boolean;
  /** The account it speaks for - an email, or a GitLab username. */
  account: string | null;
  expires_at: string | null;
  expired: boolean;
  last_verified_at: string | null;
  detail: Record<string, unknown>;
  /**
   * False when the backend has no encryption key configured. Not a fault:
   * that deployment uses its own tokens for everyone, and the interface has
   * to say so rather than offering a button that quietly does nothing.
   */
  storage_available: boolean;
}

/** What starting Google consent hands back: a URL for the user to visit. */
export interface GoogleAuthStart {
  email: string;
  /**
   * Whether the Workspace server already holds working credentials for the
   * address. When false, `message` says how to add them: that server owns
   * consent and cannot run it headless, so there is nothing to click here.
   */
  authenticated: boolean;
  /** Reserved - the Workspace server opens a browser rather than emitting a URL. */
  url: string | null;
  message: string;
}
