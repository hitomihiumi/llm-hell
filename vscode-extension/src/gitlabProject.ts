/**
 * Which GitLab project a workspace checkout belongs to, and whether a search
 * hit is one of its own.
 *
 * `search_knowledge_base` already reaches GitLab, but a query like "where is
 * the retry logic" against the whole instance surfaces every project that has
 * ever had retry logic. The workspace already answers "which one" - it is the
 * repository sitting open - so `.git/config`'s `origin` remote is turned into
 * the `namespace/project` path GitLab hits are titled with, and
 * `search_gitlab_project` (tools.ts) filters the federated result down to
 * that one repository.
 *
 * Pure and vscode-free on purpose, for the same reason `toolOutput.ts` is:
 * reading `.git/config` needs the extension host, parsing what it says does
 * not, and only the second half is worth a test that runs without one.
 * `tools.ts` does the reading and calls in here with the text.
 */

const REMOTE_URL = /^\s*url\s*=\s*(.+?)\s*$/;
const REMOTE_SECTION = /^\s*\[remote\s+"([^"]+)"\]/;

export interface GitlabProject {
  /** `namespace/subgroup/project`, the form GitLab titles a code hit with. */
  path: string;
  remote: string;
}

/**
 * The `origin` remote's URL, out of a `.git/config` file's text.
 *
 * `git config` itself is a stricter INI - sections nest with `[remote
 * "origin"]`, and a bare `[core]` further down closes the previous section.
 * That closing matters: without tracking it, a `url =` line under `[core]`
 * that follows `[remote "origin"]` would be misread as origin's.
 */
export function parseOriginUrl(configText: string): string | undefined {
  let inOrigin = false;
  for (const line of configText.split("\n")) {
    const section = REMOTE_SECTION.exec(line);
    if (section) {
      inOrigin = section[1] === "origin";
      continue;
    }
    if (/^\s*\[/.test(line)) {
      inOrigin = false;
      continue;
    }
    if (inOrigin) {
      const url = REMOTE_URL.exec(line);
      if (url) return url[1];
    }
  }
  return undefined;
}

/**
 * `git@gitlab.example.com:group/sub/project.git` or
 * `https://gitlab.example.com/group/sub/project.git` -> `group/sub/project`.
 *
 * Both forms carry the path after the host; the difference is just `:` vs
 * `/` as the separator and an optional `.git` suffix that means nothing.
 */
export function pathFromRemoteUrl(url: string): string | undefined {
  const scp = /^[\w.-]+@[\w.-]+:(.+)$/.exec(url);
  const rest = scp ? scp[1] : /^[a-z][\w+.-]*:\/\/(?:[^@/]+@)?[^/]+\/(.+)$/i.exec(url)?.[1];
  if (!rest) return undefined;
  const path = rest.replace(/\.git\/?$/, "").replace(/^\/+|\/+$/g, "");
  return path || undefined;
}

/** `.git/config`'s text, straight to the project this checkout is of - or nothing found in it. */
export function projectFromConfig(configText: string): GitlabProject | undefined {
  const remote = parseOriginUrl(configText);
  if (!remote) return undefined;
  const path = pathFromRemoteUrl(remote);
  return path ? { path, remote } : undefined;
}

/**
 * Whether a hit belongs to `project`, from whatever the search response
 * carried - the container GitLab hits are grouped under, the hit's own
 * title (`namespace/project/src/main.ts`), or the URL.
 *
 * Three checks because no one field is guaranteed: a repository hit (the
 * project itself, matched by name) has no container to check; `container`
 * is what a code or issue hit is filed under.
 */
export function belongsToProject(
  hit: { title: string; url: string | null; container?: { title: string } | null },
  project: GitlabProject,
): boolean {
  const prefix = `${project.path}/`;
  if (hit.container?.title === project.path) return true;
  if (hit.title === project.path || hit.title.startsWith(prefix)) return true;
  return Boolean(hit.url?.includes(`/${project.path}`));
}
