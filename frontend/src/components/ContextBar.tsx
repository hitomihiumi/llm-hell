import { Button, Column, Row, Text, Tooltip } from "@nmmty/dotmatrix";

import { type ContextUsage, SEGMENT_LABELS, SEGMENT_ORDER } from "../api/context";

// Grayscale-first with the two most "live" segments (files actually pulled
// in by tool calls) picked out in the theme's accent color - keeps the bar
// legible without fighting the design system's monochrome identity.
const SEGMENT_SHADES: Record<string, string> = {
  system: "var(--dm-gray-500)",
  repo_map: "var(--dm-gray-700)",
  pinned: "var(--dm-accent-500)",
  retrieved: "var(--dm-accent-700)",
  summary: "var(--dm-gray-800)",
  history: "var(--dm-gray-900)",
};

interface ContextBarProps {
  usage: ContextUsage;
  onCompactNow?: () => void;
  compacting?: boolean;
}

/** The six-segment context budget bar from the plan: hover a segment for
 * its exact token count. A per-file drill-down ("context inspector") needs
 * more than aggregate counts to be useful - that lands once the agent loop
 * (task 6) can report which files/messages actually went into each
 * segment of a real run, not before. */
export function ContextBar({ usage, onCompactNow, compacting }: ContextBarProps) {
  const denominator = Math.max(usage.total, usage.ctx_window, 1);

  return (
    <Column gap="4" style={{ width: "100%" }}>
      <Row justifyContent="between" alignItems="center" gap="8">
        <Text fontSize="2xs" color="weak">
          Контекст: {usage.total.toLocaleString("uk-UA")} / {usage.ctx_window.toLocaleString("uk-UA")} токенів
          {" "}({Math.round(usage.fraction_used * 100)}%)
          {usage.reasoning_tokens > 0 && ` · reasoning: ${usage.reasoning_tokens.toLocaleString("uk-UA")}`}
        </Text>
        {onCompactNow && (
          <Button size="s" variant="ghost" onClick={onCompactNow} disabled={compacting}>
            {compacting ? "Ущільнюємо..." : "Ущільнити зараз"}
          </Button>
        )}
      </Row>
      <Row style={{ height: 8, borderRadius: 4, overflow: "hidden", width: "100%", background: "var(--dm-gray-300)" }}>
        {SEGMENT_ORDER.map((name) => {
          const value = usage.segments[name] ?? 0;
          if (value <= 0) return null;
          return (
            <Tooltip key={name} content={`${SEGMENT_LABELS[name]}: ${value.toLocaleString("uk-UA")} токенів`}>
              <div
                tabIndex={0}
                style={{ width: `${(value / denominator) * 100}%`, background: SEGMENT_SHADES[name] }}
              />
            </Tooltip>
          );
        })}
      </Row>
    </Column>
  );
}
