import { Row } from "@nmmty/dotmatrix";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { authApi } from "../api/auth";
import { projectsApi, type Project, type TreeNode } from "../api/projects";
import { ChatPanel } from "../components/ChatPanel";
import { CodePanel, type OpenFileTab } from "../components/CodePanel";
import { Sidebar } from "../components/Sidebar";
import { useAuthStore } from "../store/auth";

export function WorkbenchPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);

  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeNode | null>(null);

  const [openTabs, setOpenTabs] = useState<OpenFileTab[]>([]);
  const [codeOpen, setCodeOpen] = useState(false);

  async function reloadProjects(selectId?: string) {
    const list = await projectsApi.list();
    setProjects(list);
    if (selectId) setActiveProjectId(selectId);
    else if (!activeProjectId && list.length > 0) setActiveProjectId(list[0].id);
  }

  async function reloadTree(projectId: string) {
    setTree(await projectsApi.tree(projectId));
  }

  useEffect(() => {
    void reloadProjects();
  }, []);

  useEffect(() => {
    setOpenTabs([]);
    setCodeOpen(false);
    if (activeProjectId) void reloadTree(activeProjectId);
    else setTree(null);
  }, [activeProjectId]);

  async function onLogout() {
    await authApi.logout();
    setUser(null);
    navigate("/login");
  }

  async function onCreateProject(name: string) {
    const project = await projectsApi.create(name);
    await reloadProjects(project.id);
  }

  async function onDeleteProject(projectId: string) {
    await projectsApi.remove(projectId);
    setActiveProjectId(null);
    await reloadProjects();
  }

  async function onTogglePin(fileId: string, pinned: boolean) {
    if (!activeProjectId) return;
    await projectsApi.setPinned(activeProjectId, fileId, pinned);
    await reloadTree(activeProjectId);
  }

  async function onOpenFile(fileId: string, path: string) {
    if (!activeProjectId) return;
    setCodeOpen(true);
    if (openTabs.some((t) => t.fileId === fileId)) return;
    const { content } = await projectsApi.fileContent(activeProjectId, fileId);
    setOpenTabs((prev) => [...prev, { fileId, path, content }]);
  }

  function onCloseTab(fileId: string) {
    setOpenTabs((prev) => prev.filter((t) => t.fileId !== fileId));
  }

  return (
    <Row height="screen" style={{ overflow: "hidden" }}>
      <Sidebar
        user={user}
        onLogout={onLogout}
        projects={projects}
        activeProjectId={activeProjectId}
        onSelectProject={setActiveProjectId}
        onCreateProject={onCreateProject}
        onDeleteProject={onDeleteProject}
        tree={tree}
        onTogglePin={onTogglePin}
        onOpenFile={onOpenFile}
        onUploaded={() => {
          if (activeProjectId) void reloadTree(activeProjectId);
          void reloadProjects();
        }}
      />

      <ChatPanel projectId={activeProjectId} />

      <CodePanel tabs={openTabs} open={codeOpen} onOpenChange={setCodeOpen} onCloseTab={onCloseTab} />
    </Row>
  );
}
