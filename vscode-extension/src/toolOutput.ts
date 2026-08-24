/**
 * What a tool is allowed to hand back, and where a path points.
 *
 * Both are pure, and both are here rather than in `tools.ts` for the usual
 * reason — that file imports `vscode` and cannot be loaded outside an
 * extension host. These are the parts worth testing: a tool result goes
 * straight into the next request, so an uncapped one is a context window
 * spent on a lockfile, and a mis-resolved path is a read of the wrong file.
 */

/**
 * How much of a tool's output travels back to the model.
 *
 * Generous enough for a source file, small enough that a `find /` or a
 * printed lockfile cannot fill the prompt. The agent gets several turns, so
 * it can ask a narrower question rather than being handed everything.
 */
export const TOOL_OUTPUT_LIMIT = 24_000;

/**
 * How long a shell command may run before it is killed.
 *
 * Bounded because a coding agent will eventually run `npm run dev`, and a
 * server that never exits would hold the turn open forever with no way to
 * tell whether it was working or hung.
 */
export const COMMAND_TIMEOUT_MS = 120_000;

/**
 * Cut to `limit`, saying so.
 *
 * The note matters as much as the cut. A model handed a silently truncated
 * file will reason about the half it can see as though that were the whole,
 * and confidently describe a function that does not end where it thinks.
 */
export function truncate(text: string, limit: number = TOOL_OUTPUT_LIMIT): string {
  if (text.length <= limit) return text;
  const cut = text.length - limit;
  return `${text.slice(0, limit)}\n\n[truncated: ${cut} more characters. Ask for a narrower range or a specific part.]`;
}

/**
 * Whether a path is already absolute, on either kind of machine.
 *
 * `node:path.isAbsolute` would answer for the host it runs on, and the answer
 * is wrong the moment an extension host on Windows is asked about `/etc/hosts`
 * or one on Linux about `C:\src`. Both forms are recognised here, because
 * which one arrives depends on the model rather than on the platform.
 */
export function isAbsolutePath(input: string): boolean {
  const path = input.trim();
  if (!path) return false;
  // POSIX, and the Windows form that omits the drive.
  if (path.startsWith("/")) return true;
  // UNC: \\server\share
  if (path.startsWith("\\\\")) return true;
  // Drive-qualified: C:\src or C:/src
  return /^[A-Za-z]:[\\/]/.test(path);
}

/**
 * Everything a finished command has to say, in one string.
 *
 * stdout and stderr both, because a compiler puts its errors on the second
 * one and a tool result that dropped them would have the agent conclude the
 * build passed.
 */
export function commandResult(
  stdout: string,
  stderr: string,
  options: { code?: number | string; timedOut?: boolean; message?: string } = {},
): string {
  const parts: string[] = [];
  if (options.timedOut) {
    parts.push(
      `[the command was still running after ${COMMAND_TIMEOUT_MS / 1000}s and was stopped]`,
    );
  } else if (options.code !== undefined && options.code !== 0) {
    parts.push(`[exit ${options.code}]`);
  }
  if (stdout.trim()) parts.push(stdout.trimEnd());
  if (stderr.trim()) parts.push(`stderr:\n${stderr.trimEnd()}`);
  if (!parts.length) {
    // A command that printed nothing and succeeded still has to say something,
    // or the agent reads the empty string as a failure and tries again.
    parts.push(options.message ?? "[no output]");
  }
  return truncate(parts.join("\n"));
}

/** The shape `search_knowledge_base` reports back, as much of it as matters. */
export interface SearchResultLine {
  /** What read_knowledge_base_result takes to fetch this result in full. */
  id: string;
  title: string;
  source: string;
  url: string | null;
  snippet: string;
}

/**
 * Search results as text for a model to read.
 *
 * Written for the agent rather than for a person: the source and the link are
 * on the same line as the title so a claim can be attributed without a second
 * lookup, and each snippet is bounded so one long transcription cannot crowd
 * out the other nine results. The id is printed too - without it, an agent
 * that wants a result’s full text has nothing to hand read_knowledge_base_result
 * and reaches for a shell command instead, which is the failure this exists
 * to close off.
 */
export function formatSearchResults(hits: SearchResultLine[], perHit = 800): string {
  if (!hits.length) {
    // Said plainly, because "nothing" and "the tool failed" lead the agent to
    // do very different things next.
    return "No results. The knowledge base has nothing on this.";
  }
  const blocks = hits.map((hit, index) => {
    const where = hit.url ? `${hit.source} — ${hit.url}` : hit.source;
    const body = hit.snippet.trim();
    return `[${index + 1}] ${hit.title} (id: ${hit.id})\n    ${where}\n${body ? truncate(body, perHit) : "    (no excerpt)"}`;
  });
  return truncate(blocks.join("\n\n"));
}
