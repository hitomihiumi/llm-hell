/**
 * Keeping an agent turn inside the model's context window.
 *
 * A coding turn does not grow gently: one `read_file` can return 24 000
 * characters, and a loop that reads four files and runs a build has spent
 * more of the window on tool output than on everything else put together.
 * Left alone it eventually sends a request the model refuses, and the turn
 * dies at whatever point that happens - which is the worst possible moment,
 * because the work up to it is already done.
 *
 * So the oldest, largest, least useful thing is folded away first, and only
 * then are whole exchanges dropped:
 *
 * 1. **Old tool results become stubs.** The model has already read them and
 *    written its conclusions into its own text; the raw output is the part it
 *    no longer needs. This is where nearly all the space is.
 * 2. **Then the oldest exchanges go**, replaced by one line saying how many.
 *
 * What is never touched: the leading system messages (the environment and
 * any attached files - the turn is meaningless without them) and the most
 * recent exchanges, which are what the model is actually reasoning about.
 *
 * Pure and vscode-free: the policy is the part worth testing, and it needs no
 * editor to decide anything.
 */

export interface CompactableMessage {
  role: string;
  content?: string;
  tool_call_id?: string;
  tool_calls?: unknown[];
}

/** Messages at the end that are never folded - the live exchange. */
export const KEEP_RECENT = 6;

/**
 * How far under budget to compact once compaction is needed.
 *
 * Compacting to exactly the budget means compacting again on the very next
 * message, so each pass buys room for several.
 */
export const TARGET_FRACTION = 0.7;

/**
 * Tokens, roughly.
 *
 * Deliberately an estimate and named like one. A real tokenizer would be a
 * dependency and a model-specific one at that, while the decision this feeds
 * - "is this close to too big" - does not need the exact number. Non-ASCII
 * costs more per character than the usual four-characters-a-token rule
 * suggests, which matters here because the questions are Ukrainian and that
 * rule would under-count them by half.
 */
export function estimateTokens(text: string): number {
  let ascii = 0;
  let wide = 0;
  for (const character of text) {
    if (character.charCodeAt(0) < 128) ascii++;
    else wide++;
  }
  return Math.ceil(ascii / 4 + wide / 2);
}

/** What one message costs, including the few tokens its envelope adds. */
export function measureMessage(message: CompactableMessage): number {
  const calls = message.tool_calls ? JSON.stringify(message.tool_calls) : "";
  return estimateTokens(message.content ?? "") + estimateTokens(calls) + 4;
}

export function measure(messages: readonly CompactableMessage[]): number {
  return messages.reduce((total, message) => total + measureMessage(message), 0);
}

export interface CompactionResult {
  messages: CompactableMessage[];
  /** Tokens reclaimed, by the same estimate. Zero when nothing was done. */
  freed: number;
  /** Tool results replaced by a stub. */
  folded: number;
  /** Whole messages removed from the front. */
  dropped: number;
}

function stub(message: CompactableMessage): CompactableMessage {
  const size = (message.content ?? "").length;
  return {
    ...message,
    content: `[earlier tool output, ${size} characters, folded away to make room. Ask again if it is needed.]`,
  };
}

/**
 * Fold the conversation until it fits, or until there is nothing left that
 * may be folded.
 *
 * Returns the messages unchanged, and `freed: 0`, whenever it already fits -
 * so the caller can tell whether anything happened without comparing arrays.
 */
export function compact(messages: readonly CompactableMessage[], budget: number): CompactionResult {
  const before = measure(messages);
  if (budget <= 0 || before <= budget) {
    return { messages: [...messages], freed: 0, folded: 0, dropped: 0 };
  }

  const target = Math.floor(budget * TARGET_FRACTION);
  const working = [...messages];
  // The leading run of system messages is the turn's ground truth.
  let head = 0;
  while (head < working.length && working[head].role === "system") head++;
  const lastUntouchable = Math.max(head, working.length - KEEP_RECENT);

  let folded = 0;
  for (let index = head; index < lastUntouchable; index++) {
    if (measure(working) <= target) break;
    const message = working[index];
    // Only a result worth more than its own stub is worth folding.
    if (message.role !== "tool" || (message.content ?? "").length < 200) continue;
    working[index] = stub(message);
    folded++;
  }

  let dropped = 0;
  while (measure(working) > target && working.length > head + KEEP_RECENT) {
    working.splice(head, 1);
    dropped++;
  }
  if (dropped) {
    working.splice(head, 0, {
      role: "system",
      content: `[${dropped} earlier message(s) in this conversation were dropped to stay inside the context window.]`,
    });
  }

  return { messages: working, freed: Math.max(0, before - measure(working)), folded, dropped };
}

/**
 * Where the context actually went.
 *
 * The ring answers "how full"; this answers "full of what", which is the
 * question anybody who sees it near the top immediately has. The categories
 * are chosen to map onto something a person can act on: tool output is
 * folded automatically, attached files can be unpinned, and a tool list of
 * several thousand tokens is a reason to switch an MCP server off.
 *
 * The marker for attached files is the sentence `attachedContext` writes at
 * the top of its message - the two are a pair, and changing one without the
 * other silently moves those tokens into the environment row.
 */
export const ATTACHMENT_MARKER = "Files the user attached";

export interface ContextCategory {
  name: string;
  tokens: number;
}

export function breakdown(
  messages: readonly CompactableMessage[],
  tools: readonly unknown[] = [],
): ContextCategory[] {
  const totals = new Map<string, number>();
  const add = (name: string, tokens: number) => {
    if (tokens > 0) totals.set(name, (totals.get(name) ?? 0) + tokens);
  };

  for (const message of messages) {
    const cost = measureMessage(message);
    if (message.role === "system") {
      add(
        (message.content ?? "").includes(ATTACHMENT_MARKER) ? "Attached files" : "Environment",
        cost,
      );
    } else if (message.role === "tool") {
      add("Tool results", cost);
    } else if (message.role === "assistant") {
      add("Assistant", cost);
    } else {
      add("Your messages", cost);
    }
  }

  // Sent in full on every request, which is why it is worth showing: a
  // handful of MCP servers can put more schema in front of the model than the
  // conversation has words.
  if (tools.length) add("Tool definitions", estimateTokens(JSON.stringify(tools)));

  return [...totals]
    .map(([name, tokens]) => ({ name, tokens }))
    .sort((a, b) => b.tokens - a.tokens);
}
