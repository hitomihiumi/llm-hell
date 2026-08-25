import assert from "node:assert/strict";
import { test } from "node:test";
import {
  containerKey,
  documentKey,
  groupReferences,
  matchesFilter,
  shortTitle,
} from "../src/hitGroups.ts";
import type { ReferenceView } from "../src/webviewProtocol.ts";

/**
 * Grouping results by the thing they actually came out of.
 *
 * The filter used to be by provider - GitLab, Drive, the knowledge base -
 * which answers a question nobody asks. Nobody wants "the GitLab ones"; they
 * want "the ones from auth-service", or "that one README". These tests pin
 * the two levels that makes, and the one property that matters most: the
 * container comes from the backend, never from parsing a title.
 */

function reference(overrides: Partial<ReferenceView> = {}): ReferenceView {
  return {
    hitId: "gitlab:code:3:a.ts:1",
    title: "test/auth-service/a.ts",
    source: "gitlab",
    url: null,
    externalId: "3:a.ts",
    container: { id: "3", title: "test/auth-service", kind: "repository" },
    ...overrides,
  };
}

const NOTHING_CITED: ReadonlySet<string> = new Set();

test("files from one repository group under it", () => {
  const groups = groupReferences(
    [
      reference(),
      reference({
        hitId: "gitlab:code:3:b.ts:9",
        title: "test/auth-service/b.ts",
        externalId: "3:b.ts",
      }),
    ],
    NOTHING_CITED,
  );

  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, "test/auth-service");
  assert.equal(groups[0].kind, "repository");
  assert.deepEqual(
    groups[0].documents.map((document) => document.title),
    ["a.ts", "b.ts"],
  );
});

test("two matches in one file are one document, not two", () => {
  /* They differ by id and share an external_id. Listing the same file twice
     is exactly what the old flat list of hits did. */
  const groups = groupReferences(
    [reference({ hitId: "gitlab:code:3:a.ts:1" }), reference({ hitId: "gitlab:code:3:a.ts:88" })],
    NOTHING_CITED,
  );

  assert.equal(groups[0].documents.length, 1);
  assert.equal(groups[0].documents[0].hitIds.length, 2);
});

test("a document's label loses the repository prefix it repeats", () => {
  /* GitLab titles a code hit `group/project/src/main.ts`. Under a row
     already saying `group/project`, repeating it costs the width that would
     have shown the filename. */
  assert.equal(shortTitle("test/auth-service/src/main.ts", "test/auth-service"), "src/main.ts");
});

test("a title that does not start with the container is left alone", () => {
  assert.equal(shortTitle("somewhere/else.ts", "test/auth-service"), "somewhere/else.ts");
});

test("a document with no container is its own top-level row, marked standalone", () => {
  /* A Drive file is not inside anything, and forcing it into a group of one
     would show its name twice - once as the group, once as its only child. */
  const groups = groupReferences(
    [
      reference({
        hitId: "google_drive:abc",
        title: "Landing gear notes",
        source: "google_drive",
        externalId: "abc",
        container: null,
      }),
    ],
    NOTHING_CITED,
  );

  assert.equal(groups.length, 1);
  assert.equal(groups[0].standalone, true);
  assert.equal(groups[0].title, "Landing gear notes");
});

test("two repositories stay apart even when a filename is shared", () => {
  const groups = groupReferences(
    [
      reference(),
      reference({
        hitId: "gitlab:code:7:a.ts:1",
        title: "other/service/a.ts",
        externalId: "7:a.ts",
        container: { id: "7", title: "other/service", kind: "repository" },
      }),
    ],
    NOTHING_CITED,
  );

  assert.equal(groups.length, 2);
});

test("what the answer cited is marked and sorts first", () => {
  /* What the answer actually used is the reason most people open the list at
     all, so it should not be somewhere in the middle of everything the search
     happened to return. */
  const groups = groupReferences(
    [
      reference({
        hitId: "gitlab:code:9:zzz.ts:1",
        title: "aaa/unused/zzz.ts",
        externalId: "9:zzz.ts",
        container: { id: "9", title: "aaa/unused", kind: "repository" },
      }),
      reference(),
    ],
    new Set(["gitlab:code:3:a.ts:1"]),
  );

  assert.equal(groups[0].title, "test/auth-service");
  assert.equal(groups[0].cited, true);
  assert.equal(groups[1].cited, false);
});

test("a container counts as used when any one file inside it was cited", () => {
  const groups = groupReferences(
    [
      reference({ hitId: "gitlab:code:3:a.ts:1", externalId: "3:a.ts" }),
      reference({
        hitId: "gitlab:code:3:b.ts:1",
        title: "test/auth-service/b.ts",
        externalId: "3:b.ts",
      }),
    ],
    new Set(["gitlab:code:3:b.ts:1"]),
  );

  assert.equal(groups[0].cited, true);
  assert.deepEqual(
    groups[0].documents.map((document) => [document.title, document.cited]),
    [
      ["b.ts", true],
      ["a.ts", false],
    ],
  );
});

// --- filtering -------------------------------------------------------------------

test("selecting a repository keeps every file in it", () => {
  const a = reference();
  const b = reference({
    hitId: "gitlab:code:3:b.ts:1",
    title: "test/auth-service/b.ts",
    externalId: "3:b.ts",
  });
  const other = reference({
    hitId: "gitlab:code:7:c.ts:1",
    externalId: "7:c.ts",
    container: { id: "7", title: "other/service", kind: "repository" },
  });

  const selected = containerKey(a);

  assert.ok(matchesFilter(a, selected));
  assert.ok(matchesFilter(b, selected));
  assert.ok(!matchesFilter(other, selected));
});

test("selecting one file keeps only that file, not its siblings", () => {
  const a = reference();
  const b = reference({
    hitId: "gitlab:code:3:b.ts:1",
    title: "test/auth-service/b.ts",
    externalId: "3:b.ts",
  });

  const selected = documentKey(a);

  assert.ok(matchesFilter(a, selected));
  assert.ok(!matchesFilter(b, selected));
});

test("selecting one file still keeps its other matches", () => {
  /* Filtering to README.md must not hide the second match inside it. */
  const first = reference({ hitId: "gitlab:code:3:a.ts:1" });
  const second = reference({ hitId: "gitlab:code:3:a.ts:88" });

  assert.ok(matchesFilter(second, documentKey(first)));
});

test("no selection keeps everything", () => {
  assert.ok(matchesFilter(reference(), null));
});

test("a hit with no external id falls back to its own id as the document", () => {
  /* Otherwise every such hit would collapse into one row keyed on undefined. */
  const a = reference({ hitId: "kb:articles:1", externalId: null, container: null });
  const b = reference({ hitId: "kb:articles:2", externalId: null, container: null });

  assert.notEqual(documentKey(a), documentKey(b));
});
