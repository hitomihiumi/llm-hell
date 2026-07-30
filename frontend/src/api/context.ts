export interface ContextUsage {
  segments: Record<string, number>;
  total: number;
  ctx_window: number;
  fraction_used: number;
  reasoning_tokens: number;
}

/** Matches `context.counter.SEGMENT_NAMES` on the backend. */
export const SEGMENT_ORDER = ["system", "repo_map", "pinned", "retrieved", "summary", "history"] as const;

export const SEGMENT_LABELS: Record<string, string> = {
  system: "Системний промпт",
  repo_map: "Карта репозиторію",
  pinned: "Закріплені файли",
  retrieved: "Прочитані файли",
  summary: "Стиснута історія",
  history: "Історія діалогу",
};

export const EMPTY_CONTEXT_USAGE: ContextUsage = {
  segments: {},
  total: 0,
  ctx_window: 0,
  fraction_used: 0,
  reasoning_tokens: 0,
};
