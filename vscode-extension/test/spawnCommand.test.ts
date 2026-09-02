import assert from "node:assert/strict";
import { test } from "node:test";
import { type CommandLookup, planSpawn, resolveOnPath } from "../src/spawnCommand.ts";

/** A Windows PATH with the exact shape that broke: nodejs ships both `npx` and `npx.CMD`. */
function windows(files: string[]): CommandLookup {
  const present = new Set(files.map((f) => f.toLowerCase()));
  return {
    windows: true,
    path: "C:\\Program Files\\nodejs;C:\\Windows\\System32",
    pathExt: ".COM;.EXE;.BAT;.CMD",
    exists: (candidate) => present.has(candidate.toLowerCase()),
    separator: ";",
    join: (dir, file) => `${dir}\\${file}`,
  };
}

function posix(files: string[] = []): CommandLookup {
  const present = new Set(files);
  return {
    windows: false,
    path: "/usr/bin:/usr/local/bin",
    pathExt: "",
    exists: (candidate) => present.has(candidate),
    separator: ":",
    join: (dir, file) => `${dir}/${file}`,
  };
}

// --- resolveOnPath ---------------------------------------------------------------

test("PATHEXT wins over the bare name", () => {
  /* The bug exactly: `C:\Program Files\nodejs` holds both `npx` (a shell
     script for Git Bash, which Windows cannot execute) and `npx.CMD` (the
     one it can). Trying the bare name first picks the wrong file. */
  const lookup = windows(["C:\\Program Files\\nodejs\\npx", "C:\\Program Files\\nodejs\\npx.CMD"]);
  assert.equal(resolveOnPath("npx", lookup), "C:\\Program Files\\nodejs\\npx.CMD");
});

test("earlier PATH entries win", () => {
  /* The extension comes back in PATHEXT's own casing, which is what happens
     on Windows too - the filesystem is case-insensitive and the candidate
     this built is the string that gets spawned. */
  const lookup = windows([
    "C:\\Program Files\\nodejs\\tool.exe",
    "C:\\Windows\\System32\\tool.exe",
  ]);
  assert.equal(resolveOnPath("tool", lookup)?.toLowerCase(), "c:\\program files\\nodejs\\tool.exe");
});

test("a command on no PATH entry resolves to nothing", () => {
  assert.equal(resolveOnPath("nope", windows([])), undefined);
});

// --- planSpawn: the Windows cases ---------------------------------------------------

test("npx goes through the shell, because a .CMD cannot be spawned directly", () => {
  /* Node throws EINVAL on spawning a .cmd without a shell - deliberately,
     since the fix for CVE-2024-27980 - so this is the one case that has to
     use one. */
  const lookup = windows(["C:\\Program Files\\nodejs\\npx.CMD"]);
  const plan = planSpawn("npx", ["-y", "@upstash/context7-mcp"], lookup);
  assert.equal(plan.shell, true);
  assert.match(plan.file, /npx\.CMD/);
  assert.match(plan.file, /@upstash\/context7-mcp/);
  assert.deepEqual(plan.args, [], "nothing left for Node to concatenate unescaped");
});

test("a real .exe is spawned directly, with its arguments kept as an array", () => {
  const lookup = windows(["C:\\Program Files\\nodejs\\node.exe"]);
  const plan = planSpawn("node", ["server.js", "--port", "1"], lookup);
  assert.equal(plan.shell, false);
  assert.equal(plan.file.toLowerCase(), "c:\\program files\\nodejs\\node.exe");
  assert.deepEqual(plan.args, ["server.js", "--port", "1"]);
});

test("an unresolvable command still gets one more try through cmd.exe", () => {
  const plan = planSpawn("mystery", ["--x"], windows([]));
  assert.equal(plan.shell, true);
  assert.match(plan.file, /mystery/);
});

test("a command given with its own path is not searched for", () => {
  const plan = planSpawn("C:\\tools\\server.exe", ["--x"], windows([]));
  assert.equal(plan.shell, false);
  assert.equal(plan.file, "C:\\tools\\server.exe");
});

test("a .cmd given with its own path still needs the shell", () => {
  const plan = planSpawn("C:\\tools\\run.cmd", [], windows([]));
  assert.equal(plan.shell, true);
});

// --- planSpawn: quoting on the shell path --------------------------------------------

test("arguments with spaces are quoted", () => {
  const plan = planSpawn("run.cmd", ["C:\\Program Files\\data"], windows([]));
  assert.match(plan.file, /"C:\\Program Files\\data"/);
});

test("a shell metacharacter cannot end the command and start another", () => {
  /* Trusted input is not the same as shell-safe input: an unquoted `&` in a
     package name would run whatever followed it. */
  const plan = planSpawn("run.cmd", ["a&calc"], windows([]));
  assert.match(plan.file, /"a&calc"/);
});

test("an embedded quote is escaped rather than closing the argument", () => {
  const plan = planSpawn("run.cmd", ['say"hi'], windows([]));
  assert.match(plan.file, /"say""hi"/);
});

test("an empty argument survives as an empty argument", () => {
  const plan = planSpawn("run.cmd", [""], windows([]));
  assert.match(plan.file, /""/);
});

// --- planSpawn: POSIX ------------------------------------------------------------

test("POSIX spawns the command as given, with no shell and no PATH games", () => {
  /* None of the above applies off Windows: `spawn` resolves PATH itself and
     there are no batch shims, so the safe direct path is always available. */
  const plan = planSpawn("npx", ["-y", "@upstash/context7-mcp"], posix());
  assert.equal(plan.shell, false);
  assert.equal(plan.file, "npx");
  assert.deepEqual(plan.args, ["-y", "@upstash/context7-mcp"]);
});
