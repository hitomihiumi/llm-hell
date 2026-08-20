import assert from "node:assert/strict";
import { test } from "node:test";
import { answerMarkdown, collapse, fileName, linkCitations } from "../src/format.ts";
import type { SearchHit, SearchResponse } from "../src/types.ts";

function hit(overrides: Partial<SearchHit> = {}): SearchHit {
  return {
    id: "gitlab:code:3",
    source: "gitlab",
    kind: "code",
    title: "services/auth/main.go",
    snippet: "func main() {}",
    url: "https://gitlab.example.com/auth/-/blob/main/main.go",
    author: null,
    timestamp: null,
    preview_pages: null,
    rank_in_source: 0,
    score: 1,
    ...overrides,
  };
}

function response(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    query_id: "q1",
    query: "where is the session cookie set",
    queries: ["where is the session cookie set"],
    hits: [hit()],
    source_status: [
      {
        source: "gitlab",
        display_name: "GitLab",
        ok: true,
        degraded: false,
        hits: 1,
        elapsed_ms: 120,
        error: null,
      },
    ],
    answer: {
      text: "It is set in `create_session` [1].",
      model: "google/gemma-4-31b-it",
      citations: [
        {
          n: 1,
          hit_id: "gitlab:code:3",
          title: "services/auth/main.go",
          url: "https://gitlab.example.com/auth/-/blob/main/main.go",
          source: "gitlab",
        },
      ],
      hits_used: 1,
      hits_dropped: 0,
      hallucinated_citations: 0,
    },
    duration_ms: 900,
    ...overrides,
  };
}

// --- file names --------------------------------------------------------------

test("a code hit keeps its path, so the tab says what the file is", () => {
  assert.equal(fileName(hit()), "services/auth/main.go");
});

test("a title with no extension gets one, or the editor highlights nothing", () => {
  assert.equal(fileName(hit({ title: "Week 8 Progress Report" })), "Week 8 Progress Report.txt");
});

test("characters a path cannot hold are replaced rather than dropped", () => {
  const name = fileName(hit({ title: 'Q3: plan <draft> | "final"' }));

  assert.ok(!/[:|"<>]/.test(name));
  assert.ok(name.includes("Q3"));
});

test("a hit with no title falls back to its id, made safe for a path", () => {
  assert.equal(fileName(hit({ title: "" })), "gitlab-code-3.txt");
});

// --- citations ---------------------------------------------------------------

test("a citation becomes a link to the result it refers to", () => {
  const linked = linkCitations("Set in `create_session` [1].", response().answer?.citations ?? []);

  assert.ok(linked.includes("[[1]](https://gitlab.example.com/auth/-/blob/main/main.go)"));
});

test("a citation that resolves to nothing stays as text", () => {
  assert.equal(linkCitations("Claimed in [7].", []), "Claimed in [7].");
});

test("a citation to a result with no link stays as text", () => {
  const citations = [
    { n: 1, hit_id: "postgres_kb:articles:4", title: "Runbook", url: null, source: "postgres_kb" },
  ];

  assert.equal(linkCitations("See [1].", citations), "See [1].");
});

// --- the answer document -----------------------------------------------------

test("the question is the heading, so the tab is legible next to a file", () => {
  assert.ok(answerMarkdown(response()).startsWith("# where is the session cookie set"));
});

test("every cited source is listed under the answer", () => {
  const markdown = answerMarkdown(response());

  assert.ok(markdown.includes("## Sources"));
  assert.ok(markdown.includes("1. [services/auth/main.go"));
});

test("the phrasings the model searched for are shown", () => {
  /* The planner rewrites the query, so a user who typed one thing gets
     results for four. Not showing that makes the result list mysterious. */
  const markdown = answerMarkdown(
    response({ queries: ["чи є гіроскоп", "F722 IMU", "F722 MPU6000"] }),
  );

  assert.ok(markdown.includes("F722 IMU"));
});

test("a source that failed is named rather than quietly missing", () => {
  const markdown = answerMarkdown(
    response({
      source_status: [
        {
          source: "gitlab",
          display_name: "GitLab",
          ok: false,
          degraded: false,
          hits: 0,
          elapsed_ms: 20,
          error: "connection refused",
        },
      ],
    }),
  );

  assert.ok(markdown.includes("unavailable: gitlab"));
});

test("no answer is an empty document, not a heading with nothing under it", () => {
  assert.equal(answerMarkdown(response({ answer: null })), "");
});

// --- the query ---------------------------------------------------------------

test("a multi-line selection becomes one line", () => {
  assert.equal(collapse("func main() {\n\tstart()\n}"), "func main() { start() }");
});

test("a selection longer than the API accepts is cut to fit", () => {
  assert.equal(collapse("x".repeat(3000)).length, 2000);
});
