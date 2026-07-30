import { Chip, CodeBlock, Column, Drawer, Row, Text } from "@nmmty/dotmatrix";

import { detectCodeLanguage } from "../lib/codeLanguage";

export interface OpenFileTab {
  fileId: string;
  path: string;
  content: string;
}

interface CodePanelProps {
  tabs: OpenFileTab[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseTab: (fileId: string) => void;
}

export function CodePanel({ tabs, open, onOpenChange, onCloseTab }: CodePanelProps) {
  const title = tabs.length <= 1 ? (tabs[0]?.path ?? "Файл") : `Файли (${tabs.length})`;

  return (
    <Drawer side="right" title={title} open={open} onOpenChange={onOpenChange}>
      <Column gap="16" padding="16" style={{ width: "min(90vw, 900px)" }}>
        {tabs.length > 1 && (
          <Row gap="4" wrap="wrap">
            {tabs.map((tab) => (
              <Chip key={tab.fileId} onRemove={() => onCloseTab(tab.fileId)} removeLabel={`Закрити ${tab.path}`}>
                {tab.path}
              </Chip>
            ))}
          </Row>
        )}
        {tabs.length > 0 ? (
          <CodeBlock
            codes={tabs.map((tab) => ({
              code: tab.content,
              language: detectCodeLanguage(tab.path),
              label: tab.path.split("/").pop(),
            }))}
            copyButton
            fullscreenButton
            lineNumbers
            syntaxTheme="mono"
          />
        ) : (
          <Text color="weak">Немає відкритих файлів.</Text>
        )}
      </Column>
    </Drawer>
  );
}
