import assert from "node:assert/strict";
import { test } from "node:test";
import {
  COMMAND_TIMEOUT_MS,
  commandResult,
  formatSearchResults,
  isAbsolutePath,
  TOOL_OUTPUT_LIMIT,
  truncate,
} from "../src/toolOutput.ts";

/**
 * What `@coder`'s tools hand back to the model.
 *
 * Every one of these is a quiet failure rather than a loud one. An uncapped
 * result spends the context window on a lockfile; a silently cut file has the
 * model describing a function that does not end where it thinks; a path
 * resolved against the wrong root reads a different file and reports on it
 * with complete confidence.
 */

// --- truncation ----------------------------------------------------------------

test("output that fits is passed through untouched", () => {
  assert.equal(truncate("const x = 1;"), "const x = 1;");
});

test("output that does not fit is cut and says so", () => {
  const cut = truncate("x".repeat(TOOL_OUTPUT_LIMIT + 500));

  assert.ok(cut.length < TOOL_OUTPUT_LIMIT + 200);
  assert.ok(cut.includes("truncated"));
});

test("the note says how much is missing, not just that something is", () => {
  /* A model told only "truncated" cannot tell whether it lost a line or a
     megabyte, and will happily answer as though it had the whole file. */
  assert.ok(truncate("y".repeat(1500), 1000).includes("500 more characters"));
});

// --- paths ---------------------------------------------------------------------

test("a POSIX absolute path is absolute", () => {
  assert.equal(isAbsolutePath("/etc/hosts"), true);
});

test("a Windows absolute path is absolute, with either slash", () => {
  assert.equal(isAbsolutePath("C:\\DEV\\llm-hell"), true);
  assert.equal(isAbsolutePath("c:/DEV/llm-hell"), true);
});

test("a UNC path is absolute", () => {
  assert.equal(isAbsolutePath("\\\\server\\share\\file.ts"), true);
});

test("a relative path is not, and that is the common case", () => {
  /* `src/main.ts` is what a model says when it means the workspace, and it
     has to be joined to the folder rather than opened from the drive root. */
  assert.equal(isAbsolutePath("src/main.ts"), false);
  assert.equal(isAbsolutePath("./config.py"), false);
  assert.equal(isAbsolutePath("config.py"), false);
});

test("nothing is not a path", () => {
  assert.equal(isAbsolutePath(""), false);
  assert.equal(isAbsolutePath("   "), false);
});

// --- command results -----------------------------------------------------------

test("a successful command returns what it printed", () => {
  assert.equal(commandResult("built in 2.1s\n", "", { code: 0 }), "built in 2.1s");
});

test("stderr is kept, because that is where a compiler puts the errors", () => {
  const result = commandResult("", "main.go:4: undefined: foo\n", { code: 2 });

  assert.ok(result.includes("exit 2"));
  assert.ok(result.includes("undefined: foo"));
});

test("a command that printed nothing still says something", () => {
  /* An empty string reads as a failure, and the agent runs it again. */
  assert.equal(commandResult("", "", { code: 0 }), "[no output]");
});

test("a killed command is reported as a timeout, not as a mysterious exit", () => {
  const result = commandResult("", "", { timedOut: true });

  assert.ok(result.includes("still running"));
  assert.ok(result.includes(String(COMMAND_TIMEOUT_MS / 1000)));
});

test("a long command output is capped like any other tool result", () => {
  const result = commandResult("z".repeat(TOOL_OUTPUT_LIMIT + 1000), "", { code: 0 });

  assert.ok(result.includes("truncated"));
});

// --- search results ------------------------------------------------------------

const HIT = {
  id: "gitlab:code:3",
  title: "services/auth/main.go",
  source: "gitlab",
  url: "https://gitlab.example.com/auth/-/blob/main/main.go",
  snippet: "func main() { start() }",
};

test("a result carries where it came from on the same line as its title", () => {
  /* The agent has to attribute a claim without a second lookup. */
  const text = formatSearchResults([HIT]);

  assert.ok(text.includes("[1] services/auth/main.go"));
  assert.ok(text.includes("gitlab"));
  assert.ok(text.includes("https://gitlab.example.com"));
});

test("a result carries its id, so a follow-up read can target it", () => {
  /* Without this an agent that wants a result's full text has nothing to
     hand read_knowledge_base_result, and reaches for a shell command
     instead - the failure this exists to close off. */
  const text = formatSearchResults([HIT]);

  assert.ok(text.includes("id: gitlab:code:3"));
});

test("a result with no permalink still lists its source", () => {
  const text = formatSearchResults([{ ...HIT, url: null }]);

  assert.ok(text.includes("gitlab"));
  assert.ok(!text.includes("null"));
});

test("nothing found says so, rather than returning an empty string", () => {
  /* "" reads to the agent as a broken tool, and it calls it again. */
  assert.ok(formatSearchResults([]).startsWith("No results"));
});

test("one enormous snippet cannot crowd out the other results", () => {
  const hits = [
    { ...HIT, snippet: "x".repeat(50_000) },
    { ...HIT, title: "second.go", snippet: "func second() {}" },
  ];

  const text = formatSearchResults(hits, 200);

  assert.ok(text.includes("second.go"));
});

test("a result with no excerpt says so instead of looking empty", () => {
  assert.ok(formatSearchResults([{ ...HIT, snippet: "" }]).includes("no excerpt"));
});
