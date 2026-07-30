import { Button, Column, Icon, Input, Row, Text, Textarea, useToast } from "@nmmty/dotmatrix";
import { useRef, useState } from "react";

import { projectsApi, uploadProjectFiles } from "../api/projects";
import { ApiError } from "../api/client";
import { collectFromDataTransfer, collectFromFileList } from "../lib/collectFiles";
import { filterCollectedFiles, type FilterReport } from "../lib/filterFiles";

interface ImportPanelProps {
  projectId: string;
  onUploaded: () => void;
}

type Status = { kind: "idle" } | { kind: "filtering" } | { kind: "uploading"; fraction: number };

export function ImportPanel({ projectId, onUploaded }: ImportPanelProps) {
  const toast = useToast();
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [lastReport, setLastReport] = useState<FilterReport | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [snippetPath, setSnippetPath] = useState("");
  const [snippetContent, setSnippetContent] = useState("");
  const folderInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function runUpload(collected: Awaited<ReturnType<typeof collectFromDataTransfer>>) {
    setStatus({ kind: "filtering" });
    const report = await filterCollectedFiles(collected);
    setLastReport(report);

    if (report.accepted.length === 0) {
      setStatus({ kind: "idle" });
      return;
    }

    setStatus({ kind: "uploading", fraction: 0 });
    try {
      await uploadProjectFiles(projectId, report.accepted, (fraction) =>
        setStatus({ kind: "uploading", fraction }),
      );
      setStatus({ kind: "idle" });
      onUploaded();
    } catch (err) {
      setStatus({ kind: "idle" });
      toast.show({
        title: "Завантаження не вдалося",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "error",
      });
    }
  }

  async function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const collected = await collectFromDataTransfer(e.dataTransfer);
    await runUpload(collected);
  }

  async function onPickFolder(e: React.ChangeEvent<HTMLInputElement>) {
    if (!e.target.files) return;
    await runUpload(collectFromFileList(e.target.files));
    e.target.value = "";
  }

  async function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    if (!e.target.files) return;
    await runUpload(collectFromFileList(e.target.files));
    e.target.value = "";
  }

  async function onAddSnippet(e: React.FormEvent) {
    e.preventDefault();
    if (!snippetPath.trim() || !snippetContent) return;
    try {
      await projectsApi.uploadSnippet(projectId, snippetPath.trim(), snippetContent);
      setSnippetPath("");
      setSnippetContent("");
      onUploaded();
    } catch (err) {
      toast.show({
        title: "Не вдалося додати фрагмент",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "error",
      });
    }
  }

  const skippedTotal =
    (lastReport?.skippedIgnored.length ?? 0) +
    (lastReport?.skippedBinary.length ?? 0) +
    (lastReport?.skippedTooLarge.length ?? 0) +
    (lastReport?.skippedOverLimits.length ?? 0);

  return (
    <Column gap="16">
      <Column
        alignItems="center"
        gap="8"
        padding="16"
        radius="8"
        borderWidth="2"
        borderStyle="dashed"
        borderColor={dragOver ? undefined : "medium"}
        palette={dragOver ? "blue" : "mono"}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <Icon name="upload" />
        <Text fontSize="s" align="center">
          Перетягніть сюди папку або файли проєкту
        </Text>
        <Row gap="8" wrap="wrap" justifyContent="center">
          <Button type="button" size="s" variant="outline" onClick={() => folderInputRef.current?.click()}>
            Обрати папку
          </Button>
          <Button type="button" size="s" variant="outline" onClick={() => fileInputRef.current?.click()}>
            Обрати файли
          </Button>
        </Row>
        <input
          ref={folderInputRef}
          type="file"
          hidden
          // @ts-expect-error non-standard attribute, supported by Chromium/Firefox
          webkitdirectory=""
          onChange={onPickFolder}
        />
        <input ref={fileInputRef} type="file" hidden multiple onChange={onPickFiles} />
      </Column>

      {status.kind === "filtering" && (
        <Text fontSize="s" color="weak">
          Перевіряємо файли...
        </Text>
      )}
      {status.kind === "uploading" && (
        <Column gap="4">
          <div
            style={{
              position: "relative",
              height: 8,
              borderRadius: 4,
              background: "var(--dm-gray-300)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                position: "absolute",
                inset: 0,
                width: `${Math.round(status.fraction * 100)}%`,
                background: "var(--dm-accent-500)",
                transition: "width 0.15s ease",
              }}
            />
          </div>
          <Text fontSize="2xs" color="weak">
            {Math.round(status.fraction * 100)}%
          </Text>
        </Column>
      )}

      {lastReport && skippedTotal > 0 && (
        <Column gap="2" padding="8" radius="4" background="surface">
          <Text fontSize="2xs" weight="medium">
            Пропущено файлів: {skippedTotal}
          </Text>
          {lastReport.skippedIgnored.length > 0 && (
            <Text fontSize="2xs" color="weak">
              За правилами ігнорування: {lastReport.skippedIgnored.length}
            </Text>
          )}
          {lastReport.skippedBinary.length > 0 && (
            <Text fontSize="2xs" color="weak">
              Бінарні: {lastReport.skippedBinary.length}
            </Text>
          )}
          {lastReport.skippedTooLarge.length > 0 && (
            <Text fontSize="2xs" color="weak">
              Перевищують ліміт розміру: {lastReport.skippedTooLarge.length}
            </Text>
          )}
          {lastReport.skippedOverLimits.length > 0 && (
            <Text fontSize="2xs" color="weak">
              Перевищено загальний ліміт проєкту: {lastReport.skippedOverLimits.length}
            </Text>
          )}
        </Column>
      )}

      <Column as="form" onSubmit={onAddSnippet} gap="8">
        <Text fontSize="xs" color="weak" uppercase tracking="wide">
          Додати фрагмент коду
        </Text>
        <Input
          placeholder="Шлях у проєкті, напр. src/fix.py"
          value={snippetPath}
          onChange={(e) => setSnippetPath(e.target.value)}
        />
        <Textarea
          placeholder="Вставте код сюди"
          rows={5}
          value={snippetContent}
          onChange={(e) => setSnippetContent(e.target.value)}
        />
        <Button type="submit" size="s">
          Додати
        </Button>
      </Column>
    </Column>
  );
}
