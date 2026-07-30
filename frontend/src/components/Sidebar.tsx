import {
  Accordion,
  AccordionItem,
  Avatar,
  Column,
  Dropdown,
  DropdownItem,
  IconButton,
  Input,
  Row,
  Text,
} from "@nmmty/dotmatrix";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import type { User } from "../api/auth";
import type { Project, TreeNode } from "../api/projects";
import { FileTree } from "./FileTree";
import { ImportPanel } from "./ImportPanel";

interface SidebarProps {
  user: User | null;
  onLogout: () => void;
  projects: Project[];
  activeProjectId: string | null;
  onSelectProject: (id: string) => void;
  onCreateProject: (name: string) => Promise<void>;
  onDeleteProject: (id: string) => void;
  tree: TreeNode | null;
  onTogglePin: (fileId: string, pinned: boolean) => void;
  onOpenFile: (fileId: string, path: string) => void;
  onUploaded: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  return `${(bytes / 1024).toFixed(1)} КБ`;
}

export function Sidebar({
  user,
  onLogout,
  projects,
  activeProjectId,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  tree,
  onTogglePin,
  onOpenFile,
  onUploaded,
}: SidebarProps) {
  const navigate = useNavigate();
  const [newProjectName, setNewProjectName] = useState("");

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newProjectName.trim()) return;
    await onCreateProject(newProjectName.trim());
    setNewProjectName("");
  }

  return (
    <Column
      as="aside"
      height="screen"
      style={{ width: 300, flexShrink: 0, borderRight: "1px solid var(--dm-border-medium)" }}
    >
      <Column gap="16" padding="12" style={{ flex: "1 1 auto", overflowY: "auto", minHeight: 0 }}>
        <Text weight="bold" fontSize="l">
          LLM-Hell
        </Text>

        <Row as="form" onSubmit={onCreate} gap="8" alignItems="center">
          <Column style={{ flex: 1 }}>
            <Input
              placeholder="Назва нового проєкту"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
            />
          </Column>
          <IconButton type="submit" icon="plus" aria-label="Створити проєкт" variant="outline" />
        </Row>

        <Column gap="2">
          {projects.map((p) => (
            <Row
              key={p.id}
              alignItems="center"
              justifyContent="between"
              gap="8"
              padding="8"
              radius="4"
              background={p.id === activeProjectId ? "raised" : undefined}
              onClick={() => onSelectProject(p.id)}
              style={{ cursor: "pointer" }}
            >
              <Column gap="0" style={{ minWidth: 0 }}>
                <Text fontSize="s" truncate>
                  {p.name}
                </Text>
                <Text fontSize="2xs" color="weak">
                  {p.file_count} файлів · {formatBytes(p.total_bytes)}
                </Text>
              </Column>
              <IconButton
                icon="trash"
                aria-label={`Видалити проєкт ${p.name}`}
                variant="ghost"
                size="s"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteProject(p.id);
                }}
              />
            </Row>
          ))}
        </Column>

        {activeProjectId && (
          <>
            <Accordion defaultValue="import">
              <AccordionItem value="import" title="Імпорт коду">
                <ImportPanel projectId={activeProjectId} onUploaded={onUploaded} />
              </AccordionItem>
            </Accordion>

            <Column gap="4">
              <Text fontSize="xs" color="weak" uppercase tracking="wide">
                Файли
              </Text>
              {tree && tree.children.length > 0 ? (
                <FileTree node={tree} onTogglePin={onTogglePin} onOpenFile={onOpenFile} />
              ) : (
                <Text fontSize="s" color="weak">
                  Проєкт порожній.
                </Text>
              )}
            </Column>
          </>
        )}
      </Column>

      {user && (
        <Row
          alignItems="center"
          gap="8"
          padding="12"
          style={{ borderTop: "1px solid var(--dm-border-medium)" }}
        >
          <Avatar name={user.username} size="s" />
          <Column gap="0" style={{ flex: 1, minWidth: 0 }}>
            <Text fontSize="s" truncate>
              {user.username}
            </Text>
            <Text fontSize="2xs" color="weak">
              {user.role === "admin" ? "Адміністратор" : "Учасник тестування"}
            </Text>
          </Column>
          <Dropdown
            trigger={<IconButton icon="more-horizontal" aria-label="Меню користувача" variant="ghost" size="s" />}
          >
            {user.role === "admin" && (
              <DropdownItem onSelect={() => navigate("/admin/invites")}>Інвайт-коди</DropdownItem>
            )}
            {user.role === "admin" && (
              <DropdownItem onSelect={() => navigate("/admin/endpoints")}>Ендпоінти моделей</DropdownItem>
            )}
            <DropdownItem onSelect={onLogout}>Вийти</DropdownItem>
          </Dropdown>
        </Row>
      )}
    </Column>
  );
}
