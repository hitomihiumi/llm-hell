import type { CollectedFile } from "./collectFiles";
import {
  MAX_FILES,
  MAX_FILE_BYTES,
  MAX_TOTAL_BYTES,
  compileIgnoreRules,
  hasBinaryExtension,
  isDefaultIgnored,
  isIgnoredByRules,
  looksBinaryContent,
} from "./ignore";

export interface FilterReport {
  accepted: CollectedFile[];
  skippedIgnored: string[];
  skippedBinary: string[];
  skippedTooLarge: string[];
  skippedOverLimits: string[];
}

/** Strips the synthetic top-level folder name browsers prepend to
 * `webkitRelativePath`/dropped-folder paths, so uploads land at the
 * project root instead of nested one level deep. */
function stripRootFolder(relativePath: string): string {
  const idx = relativePath.indexOf("/");
  return idx === -1 ? relativePath : relativePath.slice(idx + 1);
}

export async function filterCollectedFiles(files: CollectedFile[]): Promise<FilterReport> {
  const gitignoreEntry = files.find((f) => stripRootFolder(f.relativePath) === ".gitignore");
  const gitignoreRules = gitignoreEntry
    ? compileIgnoreRules(await gitignoreEntry.file.text())
    : [];

  const report: FilterReport = {
    accepted: [],
    skippedIgnored: [],
    skippedBinary: [],
    skippedTooLarge: [],
    skippedOverLimits: [],
  };

  let totalBytes = 0;

  for (const entry of files) {
    const path = stripRootFolder(entry.relativePath);
    if (!path || path === ".gitignore") continue;

    if (isDefaultIgnored(path) || isIgnoredByRules(path, gitignoreRules)) {
      report.skippedIgnored.push(path);
      continue;
    }

    if (entry.file.size > MAX_FILE_BYTES) {
      report.skippedTooLarge.push(path);
      continue;
    }

    if (hasBinaryExtension(path) || (await looksBinaryContent(entry.file))) {
      report.skippedBinary.push(path);
      continue;
    }

    if (report.accepted.length >= MAX_FILES || totalBytes + entry.file.size > MAX_TOTAL_BYTES) {
      report.skippedOverLimits.push(path);
      continue;
    }

    totalBytes += entry.file.size;
    report.accepted.push({ relativePath: path, file: entry.file });
  }

  return report;
}
