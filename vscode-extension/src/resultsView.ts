import * as vscode from "vscode";
import type { SearchHit, SearchResponse, SourceStatus } from "./types";

/**
 * The results, grouped by the source they came from.
 *
 * Grouped rather than shown in fused order, which is the opposite of what the
 * web app does, and on purpose: a flat ranked list is right when you are
 * reading an answer, and an editor sidebar is narrow, so knowing *where* a
 * result lives is what tells you whether to open it. The fused rank survives
 * inside each group.
 *
 * A source that failed is a node too. A search that quietly shows fewer
 * results because a backend is down is worse than one that says so — the same
 * reasoning the API applies when it returns a status for every source rather
 * than only the ones that worked.
 */

export type Node = SourceNode | HitNode | MessageNode;

interface SourceNode {
  type: "source";
  status: SourceStatus;
  hits: SearchHit[];
}

interface HitNode {
  type: "hit";
  hit: SearchHit;
}

interface MessageNode {
  type: "message";
  text: string;
}

export class ResultsProvider implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  private response: SearchResponse | undefined;

  get current(): SearchResponse | undefined {
    return this.response;
  }

  show(response: SearchResponse | undefined): void {
    this.response = response;
    this.changed.fire(undefined);
  }

  getChildren(node?: Node): Node[] {
    if (!this.response) return [];
    if (!node) return this.roots(this.response);
    if (node.type === "source") return node.hits.map((hit) => ({ type: "hit", hit }));
    return [];
  }

  private roots(response: SearchResponse): Node[] {
    const grouped = new Map<string, SearchHit[]>();
    for (const hit of response.hits) {
      const bucket = grouped.get(hit.source);
      if (bucket) bucket.push(hit);
      else grouped.set(hit.source, [hit]);
    }

    const nodes: Node[] = response.source_status.map((status) => ({
      type: "source",
      status,
      hits: grouped.get(status.source) ?? [],
    }));

    if (!nodes.length) {
      return [{ type: "message", text: "No sources answered." }];
    }
    if (!response.hits.length && response.source_status.every((status) => status.ok)) {
      nodes.push({ type: "message", text: "No results." });
    }
    return nodes;
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.type === "message") {
      const item = new vscode.TreeItem(node.text);
      item.contextValue = "message";
      return item;
    }
    if (node.type === "source") return sourceItem(node.status, node.hits.length);
    return hitItem(node.hit);
  }
}

function sourceItem(status: SourceStatus, count: number): vscode.TreeItem {
  const item = new vscode.TreeItem(
    status.display_name || status.source,
    // A source with nothing in it stays shut; there is nothing to open.
    count ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None,
  );

  const parts = [count === 1 ? "1 hit" : `${count} hits`, `${status.elapsed_ms} ms`];
  if (status.degraded) parts.push("partial");
  item.description = parts.join(" · ");

  if (!status.ok) {
    item.iconPath = new vscode.ThemeIcon(
      "error",
      new vscode.ThemeColor("problemsErrorIcon.foreground"),
    );
    item.description = status.error ?? "unavailable";
    item.tooltip = status.error ?? "This source did not answer.";
  } else if (status.degraded) {
    item.iconPath = new vscode.ThemeIcon(
      "warning",
      new vscode.ThemeColor("problemsWarningIcon.foreground"),
    );
    item.tooltip = "Some of this source's results are missing.";
  } else {
    item.iconPath = new vscode.ThemeIcon("database");
  }
  item.contextValue = "source";
  return item;
}

function hitItem(hit: SearchHit): vscode.TreeItem {
  const item = new vscode.TreeItem(hit.title || hit.id);
  item.description = [hit.author, formatDate(hit.timestamp)].filter(Boolean).join(" · ");
  item.iconPath = new vscode.ThemeIcon(iconFor(hit));
  item.tooltip = tooltipFor(hit);
  // Linked hits get the "open in browser" action; a Postgres row has no
  // external location and offering one would be a dead menu entry.
  item.contextValue = hit.url ? "hit.linked" : "hit";
  item.command = {
    command: "knowledgeBase.openHit",
    title: "Open",
    arguments: [hit],
  };
  return item;
}

export function iconFor(hit: SearchHit): string {
  switch (hit.kind) {
    case "code":
      return "file-code";
    case "repository":
      return "repo";
    case "commit":
      return "git-commit";
    case "email":
      return "mail";
    case "row":
      return "table";
    case "document":
      return "file";
    default:
      return "circle-outline";
  }
}

function tooltipFor(hit: SearchHit): vscode.MarkdownString {
  const tooltip = new vscode.MarkdownString();
  tooltip.appendMarkdown(`**${escapeMarkdown(hit.title || hit.id)}**\n\n`);
  if (hit.snippet) {
    // A spreadsheet's columns are its meaning, so it goes in a code fence
    // where the rows stay on their own lines instead of being reflowed.
    if (hit.snippet_format === "grid") {
      tooltip.appendCodeblock(truncate(hit.snippet, 1200), "text");
    } else {
      tooltip.appendMarkdown(`${escapeMarkdown(truncate(hit.snippet, 600))}\n\n`);
    }
  }
  tooltip.appendMarkdown(`\`${hit.source}\` · \`${hit.kind}\``);
  return tooltip;
}

function truncate(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}

function escapeMarkdown(text: string): string {
  return text.replace(/[\\`*_{}[\]()#+\-.!|]/g, "\\$&");
}

function formatDate(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleDateString();
}
