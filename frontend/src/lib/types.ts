/**
 * Hand-mirrored from backend/app/schemas/. Kept deliberately small - only
 * the fields the UI actually reads - so a backend field being added does not
 * require a change here.
 */

export type HitKind =
  | "document"
  | "email"
  | "code"
  | "repository"
  | "commit"
  | "row"
  | "unknown";

export interface SearchHit {
  id: string;
  source: string;
  kind: HitKind;
  title: string;
  snippet: string;
  url: string | null;
  author: string | null;
  timestamp: string | null;
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
  /** Free-form per-source detail. The Postgres source puts its generated SQL
   * and whether it came from the model or the fallback in here, which is one
   * of the more interesting things to show. */
  detail: Record<string, unknown>;
}

/** A prior turn, sent so a follow-up question has context. */
export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface Citation {
  n: number;
  hit_id: string;
  title: string;
  url: string | null;
  source: string;
}

export interface Source {
  key: string;
  kind: string;
  display_name: string;
  enabled: boolean;
  weight: number;
  last_checked_at: string | null;
  last_check_result: Record<string, unknown> | null;
}

export interface User {
  id: string;
  username: string;
  role: string;
  email: string | null;
  display_name: string | null;
}

/** Events emitted by POST /api/search/stream, in the order they arrive. */
export type StreamEvent =
  | {
      event: "meta";
      data: { query_id: string; query: string; answer_model: string | null };
    }
  | {
      event: "hits";
      data: {
        hits: SearchHit[];
        source_status: SourceStatus[];
        duration_ms: number;
      };
    }
  | { event: "reasoning"; data: { text: string } }
  | { event: "token"; data: { text: string } }
  | { event: "citations"; data: { citations: Citation[] } }
  | {
      event: "done";
      data: {
        duration_ms: number;
        hits_used?: number;
        hits_dropped?: number;
        hallucinated_citations?: number;
      };
    }
  | { event: "error"; data: { message: string; stage?: string } };
