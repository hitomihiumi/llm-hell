import assert from "node:assert/strict";
import { test } from "node:test";
import {
  belongsToProject,
  parseOriginUrl,
  pathFromRemoteUrl,
  projectFromConfig,
} from "../src/gitlabProject.ts";

// --- parseOriginUrl ------------------------------------------------------------

test("reads origin's url out of a real .git/config", () => {
  const config = `[core]
\trepositoryformatversion = 0
\tfilemode = false
[remote "origin"]
\turl = https://gitlab.example.com/wisco/wingmen.git
\tfetch = +refs/heads/*:refs/remotes/origin/*
[branch "main"]
\tremote = origin
\tmerge = refs/heads/main
`;
  assert.equal(parseOriginUrl(config), "https://gitlab.example.com/wisco/wingmen.git");
});

test('ignores a url under a section that is not [remote "origin"]', () => {
  /* A fork's config commonly has a second remote - `upstream` - and its url
     must never be read as though it were origin's. */
  const config = `[remote "upstream"]
\turl = https://gitlab.example.com/other/project.git
[remote "origin"]
\turl = https://gitlab.example.com/mine/project.git
`;
  assert.equal(parseOriginUrl(config), "https://gitlab.example.com/mine/project.git");
});

test("a url line under a later, unrelated section is not origin's", () => {
  /* The section-closing rule this exists for: without tracking when
     [remote "origin"] ends, a `url =` line under [core] further down would
     be misread as origin's. */
  const config = `[remote "origin"]
\tfetch = +refs/heads/*:refs/remotes/origin/*
[core]
\turl = not-a-remote-at-all
`;
  assert.equal(parseOriginUrl(config), undefined);
});

test('no [remote "origin"] section at all', () => {
  assert.equal(parseOriginUrl("[core]\n\tfilemode = false\n"), undefined);
});

test("an empty config", () => {
  assert.equal(parseOriginUrl(""), undefined);
});

// --- pathFromRemoteUrl ----------------------------------------------------------

test("an https remote", () => {
  assert.equal(pathFromRemoteUrl("https://gitlab.example.com/wisco/wingmen.git"), "wisco/wingmen");
});

test("an https remote with no .git suffix", () => {
  assert.equal(pathFromRemoteUrl("https://gitlab.com/group/project"), "group/project");
});

test("an scp-style ssh remote", () => {
  assert.equal(pathFromRemoteUrl("git@gitlab.example.com:wisco/wingmen.git"), "wisco/wingmen");
});

test("an ssh:// url remote, port and all", () => {
  assert.equal(
    pathFromRemoteUrl("ssh://git@gitlab.example.com:2222/wisco/wingmen.git"),
    "wisco/wingmen",
  );
});

test("a nested subgroup path", () => {
  assert.equal(
    pathFromRemoteUrl("https://gitlab.com/group/subgroup/project.git"),
    "group/subgroup/project",
  );
});

test("a url this cannot parse returns nothing rather than a guess", () => {
  assert.equal(pathFromRemoteUrl("not a url"), undefined);
  assert.equal(pathFromRemoteUrl("https://gitlab.com/"), undefined);
});

// --- projectFromConfig ------------------------------------------------------------

test("a realistic .git/config end to end", () => {
  const config = `[remote "origin"]\n\turl = git@gitlab.example.com:wisco/wingmen.git\n`;
  assert.deepEqual(projectFromConfig(config), {
    path: "wisco/wingmen",
    remote: "git@gitlab.example.com:wisco/wingmen.git",
  });
});

test("no origin means no project, not a thrown error", () => {
  assert.equal(projectFromConfig("[core]\n\tfilemode = false\n"), undefined);
});

// --- belongsToProject --------------------------------------------------------

const PROJECT = { path: "wisco/wingmen", remote: "https://gitlab.example.com/wisco/wingmen.git" };

test("a code hit filed under the project's container", () => {
  assert.ok(
    belongsToProject(
      { title: "src/main.ts", url: null, container: { title: "wisco/wingmen" } },
      PROJECT,
    ),
  );
});

test("the repository hit itself, titled with the project's own path", () => {
  assert.ok(belongsToProject({ title: "wisco/wingmen", url: null }, PROJECT));
});

test("a title carrying the project path as a prefix", () => {
  assert.ok(belongsToProject({ title: "wisco/wingmen/README.md", url: null }, PROJECT));
});

test("a url that mentions the project path", () => {
  assert.ok(
    belongsToProject(
      { title: "README.md", url: "https://gitlab.example.com/wisco/wingmen/-/blob/main/README.md" },
      PROJECT,
    ),
  );
});

test("a different project is not a match", () => {
  assert.ok(
    !belongsToProject(
      { title: "src/main.ts", url: null, container: { title: "other/project" } },
      PROJECT,
    ),
  );
});

test("a same-named file in a different project is not a false positive", () => {
  /* wingmen vs wingmen-legacy: the prefix check has to require the
     separator, or a project whose name is a prefix of another's would leak
     into it. */
  const decoy = { path: "wisco/wingmen-legacy", remote: "" };
  assert.ok(!belongsToProject({ title: "wisco/wingmen/README.md", url: null }, decoy));
});
