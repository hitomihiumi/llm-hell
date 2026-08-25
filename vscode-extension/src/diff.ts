/**
 * A line diff, for showing what a `write_file` actually changed.
 *
 * "Wrote 1240 bytes to sorting.ts" says nothing about what moved. The whole
 * reason to confirm a write - or to read one after the fact - is to see the
 * change, and a byte count is the one summary that cannot be checked.
 *
 * Hand-written rather than a dependency, for the same reason `markdown.ts`
 * is: this needs one algorithm over two string arrays, and the alternative is
 * bundling and auditing a diff library into a webview. The output is a
 * unified-style line list, which is all the card renders.
 *
 * Deliberately bounded. An agent rewriting a generated file produces a diff
 * of thousands of lines, and neither the LCS table nor the transcript should
 * grow with it - so both the comparison and the rendering give up early and
 * say that they did, rather than locking the extension host or flooding the
 * panel.
 */

export interface DiffLine {
  kind: "add" | "remove" | "context" | "gap";
  text: string;
}

export interface FileDiff {
  lines: DiffLine[];
  added: number;
  removed: number;
  /** True when the comparison or the rendering was cut short. */
  truncated: boolean;
}

/**
 * Above this many changed lines on either side, the quadratic LCS table stops
 * being something to run on a UI thread. 5000x5000 is 25M cells - around the
 * point where the comparison stops being instant, and well past any change a
 * person is going to read line by line.
 *
 * This is a guard against locking the extension host, not a display limit:
 * every changed line that is compared is shown.
 */
const MAX_COMPARED_LINES = 5000;

/** Unchanged lines kept either side of a change, as in a unified diff. */
const CONTEXT = 3;

export function diffLines(before: string, after: string): FileDiff {
  const a = split(before);
  const b = split(after);

  // Compared after normalising, not before. A model rewriting a file
  // unchanged is common, and so is one that rewrites it with the other
  // platform's line endings or drops the final newline - none of those are
  // changes, and comparing the raw strings reported all three as one.
  if (a.length === b.length && a.every((line, index) => line === b[index])) {
    return { lines: [], added: 0, removed: 0, truncated: false };
  }

  // Common prefix and suffix are stripped before the expensive part. A
  // one-line change in a long file leaves two short middles to compare
  // instead of two long ones.
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  ) {
    tail++;
  }

  const midA = a.slice(head, a.length - tail);
  const midB = b.slice(head, b.length - tail);

  if (midA.length > MAX_COMPARED_LINES || midB.length > MAX_COMPARED_LINES) {
    return {
      lines: [
        {
          kind: "gap",
          text: `${midA.length} lines replaced by ${midB.length} — too large to diff`,
        },
      ],
      added: midB.length,
      removed: midA.length,
      truncated: true,
    };
  }

  const ops = align(midA, midB);

  // Put the untouched prefix and suffix back as context, so a change is shown
  // where it sits rather than floating with no surroundings.
  const full: DiffLine[] = [
    ...a.slice(0, head).map((text): DiffLine => ({ kind: "context", text })),
    ...ops,
    ...a.slice(a.length - tail).map((text): DiffLine => ({ kind: "context", text })),
  ];

  const added = full.filter((line) => line.kind === "add").length;
  const removed = full.filter((line) => line.kind === "remove").length;
  const { lines, truncated } = collapse(full);
  return { lines, added, removed, truncated };
}

function split(text: string): string[] {
  if (!text) return [];
  // A trailing newline is a line terminator, not an empty last line - keeping
  // it would report a phantom change on every file that ends properly.
  const normalised = text.replace(/\r\n/g, "\n").replace(/\n$/, "");
  return normalised.split("\n");
}

/** Longest common subsequence, walked back into add/remove/context lines. */
function align(a: string[], b: string[]): DiffLine[] {
  const rows = a.length;
  const cols = b.length;
  // One row of the table at a time would be enough for the length, but the
  // walk back needs the whole thing.
  const table: number[][] = Array.from({ length: rows + 1 }, () =>
    new Array<number>(cols + 1).fill(0),
  );
  for (let i = rows - 1; i >= 0; i--) {
    for (let j = cols - 1; j >= 0; j--) {
      table[i][j] =
        a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < rows && j < cols) {
    if (a[i] === b[j]) {
      lines.push({ kind: "context", text: a[i] });
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      lines.push({ kind: "remove", text: a[i] });
      i++;
    } else {
      lines.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  while (i < rows) lines.push({ kind: "remove", text: a[i++] });
  while (j < cols) lines.push({ kind: "add", text: b[j++] });
  return lines;
}

/**
 * Replace long runs of *unchanged* lines with a gap.
 *
 * Without this, changing one line in a 900-line file renders 900 lines to say
 * so. Note what this does not do: every added and removed line survives, however
 * many there are. Collapsing what did not change is not shortening the diff,
 * and the pane scrolls.
 */
function collapse(lines: DiffLine[]): { lines: DiffLine[]; truncated: boolean } {
  const keep = new Array<boolean>(lines.length).fill(false);
  lines.forEach((line, index) => {
    if (line.kind === "add" || line.kind === "remove") {
      for (let at = index - CONTEXT; at <= index + CONTEXT; at++) {
        if (at >= 0 && at < lines.length) keep[at] = true;
      }
    }
  });

  const out: DiffLine[] = [];
  let skipped = 0;
  for (let index = 0; index < lines.length; index++) {
    if (keep[index]) {
      if (skipped) {
        out.push({ kind: "gap", text: `${skipped} unchanged ${skipped === 1 ? "line" : "lines"}` });
        skipped = 0;
      }
      out.push(lines[index]);
    } else {
      skipped++;
    }
  }
  if (skipped) {
    out.push({ kind: "gap", text: `${skipped} unchanged ${skipped === 1 ? "line" : "lines"}` });
  }
  return { lines: out, truncated: false };
}
