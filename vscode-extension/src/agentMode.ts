export type AgentMode = "manual" | "assisted" | "autonomous";

/** The tools that change something, and so are the only ones ever asked about. */
const CHANGING_TOOLS: ReadonlySet<string> = new Set(["write_file", "run_terminal"]);

/**
 * Which tools each mode stops to ask about.
 *
 * The three names have to mean three different things, and for a while two of
 * them did not: `assisted` asked about exactly what `manual` did, and
 * `autonomous` still asked before every terminal command - so the mode a user
 * picks precisely because they do not want to be interrupted interrupted them
 * anyway, on the one tool an agent reaches for most.
 *
 * `autonomous` asking about nothing is the point of the setting, and it is
 * not unguarded: `executeTool` refuses a file path outside the workspace
 * before this is consulted, and the mode is one the user selects deliberately
 * - with `assisted` sitting between as the one that lets workspace writes
 * through and still stops at the terminal.
 *
 * Kept here, away from `tools.ts`, because this file imports nothing from
 * `vscode` and so the policy can be tested directly.
 */
const ASKS_ABOUT: Record<AgentMode, ReadonlySet<string>> = {
  manual: CHANGING_TOOLS,
  assisted: new Set(["run_terminal"]),
  autonomous: new Set(),
};

export function needsApproval(mode: AgentMode, toolName: string): boolean {
  return ASKS_ABOUT[mode].has(toolName);
}
