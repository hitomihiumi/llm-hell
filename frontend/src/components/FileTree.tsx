import { Checkbox, Column, Icon, Row, Text } from "@nmmty/dotmatrix";
import { useState } from "react";

import type { TreeNode } from "../api/projects";

interface FileTreeProps {
  node: TreeNode;
  onTogglePin: (fileId: string, pinned: boolean) => void;
  onOpenFile: (fileId: string, path: string) => void;
  depth?: number;
}

function DirRow({ name, depth, children }: { name: string; depth: number; children: React.ReactNode }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <Column gap="0">
      <Row
        alignItems="center"
        gap="4"
        paddingY="4"
        onClick={() => setExpanded((v) => !v)}
        style={{ cursor: "pointer", paddingLeft: depth * 14 }}
      >
        <Icon name={expanded ? "chevron-down" : "chevron-right"} size="s" />
        <Text weight="medium" fontSize="s">
          {name}
        </Text>
      </Row>
      {expanded && children}
    </Column>
  );
}

export function FileTree({ node, onTogglePin, onOpenFile, depth = 0 }: FileTreeProps) {
  return (
    <Column gap="0">
      {node.children.map((child) =>
        child.type === "dir" ? (
          <DirRow key={child.path} name={child.name} depth={depth}>
            <FileTree node={child} onTogglePin={onTogglePin} onOpenFile={onOpenFile} depth={depth + 1} />
          </DirRow>
        ) : (
          <Row
            key={child.path}
            alignItems="center"
            gap="8"
            paddingY="4"
            onClick={() => child.file_id && onOpenFile(child.file_id, child.path)}
            style={{ cursor: "pointer", paddingLeft: depth * 14 + 20 }}
          >
            <Checkbox
              checked={child.pinned}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => child.file_id && onTogglePin(child.file_id, e.target.checked)}
              aria-label={`Закріпити файл ${child.name} у контексті`}
            />
            <Text fontSize="s" truncate style={{ flex: 1 }}>
              {child.name}
            </Text>
            <Text fontSize="2xs" color="weak">
              ~{child.token_count.toLocaleString("uk-UA")}
            </Text>
          </Row>
        ),
      )}
    </Column>
  );
}
