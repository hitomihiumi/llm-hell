/**
 * How to actually start a configured MCP server's command, on the platform
 * this happens to be running on.
 *
 * Worth its own file, and its own tests, because getting it wrong is silent:
 * the server never starts, no tool from it is ever published, and the model
 * answers "I don't have that tool" - which reads as a missing feature rather
 * than a failed process. That is exactly what `npx -y @upstash/context7-mcp`
 * did on Windows.
 *
 * Two Windows facts drive all of this, and neither applies to POSIX:
 *
 * 1. `npx` is `npx.CMD`. `spawn` resolves a bare name against PATH but not
 *    against PATHEXT, so it looks for a file literally called `npx` - and
 *    finds either nothing (`ENOENT`) or, where Git for Windows has installed
 *    one, a shell script Windows cannot execute.
 * 2. Node refuses to spawn a `.cmd` or `.bat` without a shell at all - it
 *    throws `EINVAL`, deliberately, since the fix for CVE-2024-27980.
 *
 * So a batch shim has to go through `cmd.exe`, and that is the one case where
 * the shell is used. A real `.exe` is spawned directly, with its arguments
 * passed as an array and never parsed by anything.
 */

export interface SpawnPlan {
  file: string;
  args: string[];
  shell: boolean;
}

/** What a Windows PATH search needs to know, injected so this can be tested off Windows. */
export interface CommandLookup {
  windows: boolean;
  path: string;
  pathExt: string;
  /** Whether this exact path exists as a file. */
  exists: (candidate: string) => boolean;
  separator: string;
  join: (dir: string, file: string) => string;
}

/**
 * The first executable on PATH matching `command`, trying PATHEXT's
 * extensions **before** the bare name.
 *
 * The order matters on exactly the case that broke: `C:\Program Files\nodejs`
 * holds both `npx` (a shell script, for Git Bash) and `npx.CMD` (the one
 * Windows can run). Trying the bare name first finds the wrong one.
 */
export function resolveOnPath(command: string, lookup: CommandLookup): string | undefined {
  const extensions = lookup.pathExt.split(";").filter(Boolean);
  for (const dir of lookup.path.split(lookup.separator).filter(Boolean)) {
    for (const extension of [...extensions, ""]) {
      const candidate = lookup.join(dir, command + extension);
      if (lookup.exists(candidate)) return candidate;
    }
  }
  return undefined;
}

const BATCH = /\.(cmd|bat)$/i;

/**
 * Quoting for the one path that has to go through `cmd.exe`.
 *
 * With `shell: true` Node concatenates arguments rather than escaping them
 * (its own DEP0190 says so), so the quoting is ours to do. These arguments
 * come from the user's own `settings.json` - the same trust as any other
 * setting - but "trusted" is not "shell-safe": an unquoted `&` in a package
 * name would still end the command and start another.
 */
function quoteForShell(argument: string): string {
  if (argument === "") return '""';
  if (!/[\s"&|<>^()%!]/.test(argument)) return argument;
  return `"${argument.replace(/"/g, '""')}"`;
}

/**
 * How to spawn `command args...`: directly wherever that works, through the
 * shell only where Windows leaves no choice.
 */
export function planSpawn(
  command: string,
  args: readonly string[],
  lookup: CommandLookup,
): SpawnPlan {
  if (!lookup.windows) return { file: command, args: [...args], shell: false };

  const hasOwnPath = /[\\/]/.test(command);
  const resolved = hasOwnPath ? command : resolveOnPath(command, lookup);
  const target = resolved ?? command;

  // A batch shim cannot be spawned directly on Node any more, so it goes
  // through cmd.exe with the whole line quoted by hand and no argument array
  // for Node to concatenate unescaped behind us. A command PATH could not
  // resolve at all goes the same way rather than failing here - cmd.exe gets
  // one more try at finding it, and its error is a better one than ours.
  if (BATCH.test(target) || resolved === undefined) {
    const line = [target, ...args].map(quoteForShell).join(" ");
    return { file: line, args: [], shell: true };
  }

  return { file: target, args: [...args], shell: false };
}
