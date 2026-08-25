import type { SearchHit } from "@/lib/types";

/**
 * The results of one search, grouped the way a person thinks about them.
 *
 * Filtering by source used to mean filtering by provider - GitLab, Drive, the
 * knowledge base - which answers a question nobody asks. Nobody wants "the
 * GitLab ones"; they want "the ones from auth-service", or "that one README".
 * So the grouping is by the thing a document actually lives in, and then by
 * the document itself.
 *
 * Two levels, because both are real: several files match inside one
 * repository, and the same file can match in several places. A flat list of
 * hits shows `auth-service/README.md` three times and never names the
 * repository; a flat list of repositories cannot say which file answered.
 *
 * The container comes from the backend - see `HitContainer` in
 * `backend/app/schemas/search.py`. It is deliberately not derived here: a
 * project path contains slashes of its own, so there is no prefix rule that
 * separates `group/project` from `group/project/src/main.ts` reliably, and
 * the connector already knows the answer.
 */

export interface DocumentGroup {
  key: string;
  /** The document's own name, with the container's prefix removed. */
  title: string;
  source: string;
  hits: SearchHit[];
  cited: boolean;
}

export interface ContainerGroup {
  key: string;
  title: string;
  source: string;
  kind: string;
  documents: DocumentGroup[];
  hits: SearchHit[];
  cited: boolean;
  /**
   * True when this is one document rather than a group of them - a Drive file
   * is not "inside" anything. Rendered as a single row, without a second
   * level that would only repeat its own name back at it.
   */
  standalone: boolean;
}

/** The document a hit belongs to. Two matches in one file share this. */
export function documentKey(hit: SearchHit): string {
  return hit.external_id
    ? `${hit.source}:doc:${hit.external_id}`
    : `hit:${hit.id}`;
}

/** The repository or table a hit belongs to, or its own document when none. */
export function containerKey(hit: SearchHit): string {
  return hit.container
    ? `${hit.source}:box:${hit.container.id}`
    : documentKey(hit);
}

/**
 * Strip the container's name off the front of a document's title.
 *
 * GitLab titles a code hit `group/project/src/main.ts` - the repository name
 * with the file path appended. Under a row already saying `group/project`,
 * repeating it wastes the width that would have shown the filename.
 */
export function shortTitle(
  title: string,
  containerTitle: string | undefined,
): string {
  if (!containerTitle) return title;
  const prefix = `${containerTitle}/`;
  return title.startsWith(prefix) ? title.slice(prefix.length) : title;
}

export function groupHits(
  hits: SearchHit[],
  citedHitIds: readonly string[],
): ContainerGroup[] {
  const cited = new Set(citedHitIds);
  const containers = new Map<string, ContainerGroup>();
  const documents = new Map<string, DocumentGroup>();

  for (const hit of hits) {
    const boxKey = containerKey(hit);
    const docKey = documentKey(hit);
    const isCited = cited.has(hit.id);

    let container = containers.get(boxKey);
    if (!container) {
      container = {
        key: boxKey,
        title: hit.container?.title ?? hit.title,
        source: hit.source,
        kind: hit.container?.kind ?? "document",
        documents: [],
        hits: [],
        cited: false,
        standalone: !hit.container,
      };
      containers.set(boxKey, container);
    }
    container.hits.push(hit);
    container.cited ||= isCited;

    let document = documents.get(docKey);
    if (!document) {
      document = {
        key: docKey,
        title: shortTitle(hit.title, hit.container?.title),
        source: hit.source,
        hits: [],
        cited: false,
      };
      documents.set(docKey, document);
      container.documents.push(document);
    }
    document.hits.push(hit);
    document.cited ||= isCited;
  }

  // Cited first, then alphabetically. What the answer actually leaned on is
  // the reason most people open this list at all, so it should not be
  // somewhere in the middle of everything the search happened to return.
  const byUse = <T extends { cited: boolean; title: string }>(a: T, b: T) =>
    a.cited === b.cited ? a.title.localeCompare(b.title) : a.cited ? -1 : 1;

  const groups = [...containers.values()].sort(byUse);
  for (const group of groups) group.documents.sort(byUse);
  return groups;
}

/** Whether a hit survives the current filter. `null` means no filter. */
export function matchesFilter(
  hit: SearchHit,
  selected: string | null,
): boolean {
  if (!selected) return true;
  return containerKey(hit) === selected || documentKey(hit) === selected;
}
