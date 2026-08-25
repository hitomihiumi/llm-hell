import type { ReferenceView } from "./webviewProtocol.ts";

/**
 * The results of one search, grouped the way a person thinks about them.
 *
 * "Filter by source" used to mean the provider - GitLab, Drive, the knowledge
 * base - which answers a question nobody asks. Nobody wants "the GitLab
 * ones"; they want "the ones from auth-service", or "that README". So the
 * grouping is by the thing the document actually lives in, and then by the
 * document itself.
 *
 * Two levels, because both are real: several files match inside one
 * repository, and the same file can match in several places. A flat list of
 * hits shows `auth-service/README.md` three times and buries the repository
 * it came from; a flat list of repositories cannot say which file answered.
 *
 * The container comes from the backend rather than being parsed out of a
 * title here - see `HitContainer` in `backend/app/schemas/search.py`. A
 * project path contains slashes of its own, so there is no prefix rule that
 * separates `group/project` from `group/project/src/main.ts` reliably.
 */

export interface DocumentGroup {
  /** What a filter selection stores. Stable across a response. */
  key: string;
  /** The document's own name, with the container's prefix removed. */
  title: string;
  source: string;
  hitIds: string[];
  /** Whether the answer actually cited any of this document's hits. */
  cited: boolean;
}

export interface ContainerGroup {
  key: string;
  title: string;
  source: string;
  kind: string;
  documents: DocumentGroup[];
  hitIds: string[];
  cited: boolean;
  /**
   * True when this is one document rather than a group of them - a Drive
   * file is not "inside" anything. Rendered as a single row, without the
   * second level that would repeat its own name back at it.
   */
  standalone: boolean;
}

/** The document a hit belongs to. Two matches in one file share this. */
export function documentKey(reference: ReferenceView): string {
  return reference.externalId
    ? `${reference.source}:doc:${reference.externalId}`
    : `hit:${reference.hitId}`;
}

/** The repository or table a hit belongs to, or its own document when none. */
export function containerKey(reference: ReferenceView): string {
  return reference.container
    ? `${reference.source}:box:${reference.container.id}`
    : documentKey(reference);
}

/**
 * Strip the container's name off the front of a document's title.
 *
 * GitLab titles a code hit `group/project/src/main.ts` - the repository name
 * with the file path appended. Under a row already saying `group/project`,
 * repeating it costs the width that would have shown the filename, which in a
 * 300px sidebar is the whole difference between readable and not.
 */
export function shortTitle(title: string, containerTitle: string | undefined): string {
  if (!containerTitle) return title;
  const prefix = `${containerTitle}/`;
  return title.startsWith(prefix) ? title.slice(prefix.length) : title;
}

export function groupReferences(
  references: ReferenceView[],
  citedHitIds: ReadonlySet<string>,
): ContainerGroup[] {
  const containers = new Map<string, ContainerGroup>();
  const documents = new Map<string, DocumentGroup>();

  for (const reference of references) {
    const boxKey = containerKey(reference);
    const docKey = documentKey(reference);
    const cited = citedHitIds.has(reference.hitId);

    let container = containers.get(boxKey);
    if (!container) {
      container = {
        key: boxKey,
        title: reference.container?.title ?? reference.title,
        source: reference.source,
        kind: reference.container?.kind ?? "document",
        documents: [],
        hitIds: [],
        cited: false,
        standalone: !reference.container,
      };
      containers.set(boxKey, container);
    }
    container.hitIds.push(reference.hitId);
    container.cited ||= cited;

    let document = documents.get(docKey);
    if (!document) {
      document = {
        key: docKey,
        title: shortTitle(reference.title, reference.container?.title),
        source: reference.source,
        hitIds: [],
        cited: false,
      };
      documents.set(docKey, document);
      container.documents.push(document);
    }
    document.hitIds.push(reference.hitId);
    document.cited ||= cited;
  }

  // Cited first, then alphabetically. What the answer actually used is the
  // reason most people open this list at all, so it should not be somewhere
  // in the middle of everything the search happened to return.
  const byUse = <T extends { cited: boolean; title: string }>(a: T, b: T) =>
    a.cited === b.cited ? a.title.localeCompare(b.title) : a.cited ? -1 : 1;

  const groups = [...containers.values()].sort(byUse);
  for (const group of groups) group.documents.sort(byUse);
  return groups;
}

/** Whether a hit survives the current filter. `null` means no filter. */
export function matchesFilter(reference: ReferenceView, selected: string | null): boolean {
  if (!selected) return true;
  return containerKey(reference) === selected || documentKey(reference) === selected;
}
