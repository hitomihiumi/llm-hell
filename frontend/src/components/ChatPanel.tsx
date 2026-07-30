import {
  Badge,
  Button,
  Card,
  Column,
  Heading,
  IconButton,
  InlineCode,
  Input,
  Row,
  Select,
  Text,
  Textarea,
} from "@nmmty/dotmatrix";
import { useState } from "react";

import type { PlanStepData, ReasoningLevel, RunMode } from "../api/runs";
import { ContextBar } from "./ContextBar";
import { type ChatEntry, useRunChat } from "../hooks/useRunChat";

const REASONING_OPTIONS = [
  { value: "off", label: "Вимкнено" },
  { value: "low", label: "Низький" },
  { value: "medium", label: "Середній" },
  { value: "high", label: "Високий" },
];

const MODE_OPTIONS = [
  { value: "approve_plan", label: "Затвердження плану" },
  { value: "stepwise", label: "Покроково" },
  { value: "autopilot", label: "Автопілот" },
];

function AssistantBubble({ entry }: { entry: Extract<ChatEntry, { kind: "assistant" }> }) {
  const [showReasoning, setShowReasoning] = useState(false);
  return (
    <Card padding="12" gap="8" style={{ maxWidth: "80%" }}>
      {entry.reasoning && (
        <Column gap="4">
          <Button size="s" variant="ghost" onClick={() => setShowReasoning((v) => !v)}>
            {showReasoning ? "Сховати міркування ▾" : "Показати міркування ▸"}
          </Button>
          {showReasoning && (
            <Text fontSize="s" color="weak" style={{ whiteSpace: "pre-wrap" }}>
              {entry.reasoning}
            </Text>
          )}
        </Column>
      )}
      <Text style={{ whiteSpace: "pre-wrap" }}>{entry.text || (entry.done ? "" : "...")}</Text>
    </Card>
  );
}

function ToolEntry({
  entry,
  onDecide,
}: {
  entry: Extract<ChatEntry, { kind: "tool" }>;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  return (
    <Card padding="12" gap="8" background="surface">
      <Row gap="8" alignItems="center">
        <InlineCode>{entry.name}</InlineCode>
        <Badge variant={entry.status === "ok" ? "success" : entry.status === "error" ? "error" : "neutral"}>
          {entry.status === "pending" ? "виконується..." : entry.status === "ok" ? "успішно" : "помилка"}
        </Badge>
      </Row>
      <Text fontSize="s" color="weak" style={{ whiteSpace: "pre-wrap" }}>
        {JSON.stringify(entry.arguments)}
      </Text>
      {entry.result && (
        <Text fontSize="s" color="weak" style={{ whiteSpace: "pre-wrap", maxHeight: 160, overflowY: "auto" }}>
          {entry.result}
        </Text>
      )}
      {entry.needsApproval && (
        <Row gap="8">
          <Button size="s" onClick={() => onDecide("approve")}>
            Дозволити
          </Button>
          <Button size="s" variant="outline" onClick={() => onDecide("reject")}>
            Відхилити
          </Button>
        </Row>
      )}
    </Card>
  );
}

function PlanCard({
  entry,
  editable,
  onDecide,
}: {
  entry: Extract<ChatEntry, { kind: "plan" }>;
  editable: boolean;
  onDecide: (decision: "approve" | "reject", steps?: PlanStepData[]) => void;
}) {
  const [steps, setSteps] = useState<PlanStepData[]>(entry.steps);

  function updateStep(index: number, patch: Partial<PlanStepData>) {
    setSteps((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  }

  return (
    <Card padding="16" gap="12">
      <Heading as="h3" fontSize="s">
        {entry.escalated ? "Оновлений план (після помилки кроку)" : "План виконання"}
      </Heading>
      <Column gap="12">
        {steps.map((step, i) => (
          <Column key={step.id} gap="4" padding="8" radius="4" background="surface">
            <Text fontSize="xs" color="weak" uppercase>
              Крок {i + 1}
            </Text>
            {editable ? (
              <>
                <Input value={step.title} onChange={(e) => updateStep(i, { title: e.target.value })} />
                <Input
                  value={step.done_when}
                  onChange={(e) => updateStep(i, { done_when: e.target.value })}
                  placeholder="Умова завершення кроку"
                />
              </>
            ) : (
              <>
                <Text weight="medium">{step.title}</Text>
                <Text fontSize="s" color="weak">
                  Завершено, коли: {step.done_when}
                </Text>
              </>
            )}
            {step.files.length > 0 && (
              <Text fontSize="2xs" color="weak">
                Файли: {step.files.join(", ")}
              </Text>
            )}
          </Column>
        ))}
      </Column>
      {editable && (
        <Row gap="8">
          <Button onClick={() => onDecide("approve", steps)}>Затвердити план</Button>
          <Button variant="outline" onClick={() => onDecide("reject")}>
            Відхилити
          </Button>
        </Row>
      )}
    </Card>
  );
}

interface ChatPanelProps {
  projectId: string | null;
}

export function ChatPanel({ projectId }: ChatPanelProps) {
  const chat = useRunChat(projectId);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<RunMode>("approve_plan");
  const [reasoningPlanner, setReasoningPlanner] = useState<ReasoningLevel>("medium");
  const [reasoningExecutor, setReasoningExecutor] = useState<ReasoningLevel>("off");

  const isActive = chat.run !== null && !chat.finished;

  async function onSend(e: React.FormEvent) {
    e.preventDefault();
    if (!draft.trim() || !projectId) return;
    await chat.startRun({ taskText: draft.trim(), mode, reasoningPlanner, reasoningExecutor });
    setDraft("");
  }

  return (
    <Column style={{ flex: "1 1 auto", minWidth: 0 }} height="screen">
      <Row gap="12" padding="12" alignItems="end" style={{ borderBottom: "1px solid var(--dm-border-medium)" }}>
        <Select
          label="Режим"
          value={mode}
          onChange={(v) => setMode(v as RunMode)}
          options={MODE_OPTIONS}
          disabled={isActive}
        />
        <Select
          label="Reasoning (planner)"
          value={reasoningPlanner}
          onChange={(v) => setReasoningPlanner(v as ReasoningLevel)}
          options={REASONING_OPTIONS}
          disabled={isActive}
        />
        <Select
          label="Reasoning (executor)"
          value={reasoningExecutor}
          onChange={(v) => setReasoningExecutor(v as ReasoningLevel)}
          options={REASONING_OPTIONS}
          disabled={isActive}
        />
        {isActive && (
          <Button variant="outline" onClick={() => chat.stopRun()}>
            Зупинити
          </Button>
        )}
      </Row>

      <Column gap="16" padding="24" style={{ flex: 1, overflowY: "auto" }}>
        {chat.entries.length === 0 && (
          <Column gap="8" alignItems="center" style={{ margin: "40px auto", maxWidth: 480 }}>
            <Heading as="h2" fontSize="l" align="center">
              Опишіть завдання для моделей
            </Heading>
            <Text color="weak" align="center">
              GLM 4.7 спроєктує план, GLM 4.7 Flash виконає його на імпортованому коді.
            </Text>
          </Column>
        )}

        {chat.entries.map((entry, i) => {
          switch (entry.kind) {
            case "user":
              return (
                <Row key={i} justifyContent="end">
                  <Card padding="12" background="raised" style={{ maxWidth: "80%" }}>
                    <Text style={{ whiteSpace: "pre-wrap" }}>{entry.text}</Text>
                  </Card>
                </Row>
              );
            case "plan":
              return (
                <PlanCard
                  key={i}
                  entry={entry}
                  editable={chat.awaitingPlanApproval && i === chat.entries.length - 1}
                  onDecide={chat.decidePlan}
                />
              );
            case "step":
              return (
                <Text key={i} fontSize="xs" color="weak" uppercase tracking="wide" align="center">
                  Крок {entry.index + 1}: {entry.title}
                </Text>
              );
            case "assistant":
              return <AssistantBubble key={i} entry={entry} />;
            case "tool":
              return <ToolEntry key={i} entry={entry} onDecide={chat.decideStep} />;
            case "error":
              return (
                <Text key={i} color="strong" palette="red">
                  {entry.message}
                </Text>
              );
            case "status":
              return (
                <Text key={i} color="weak" align="center" fontSize="s">
                  {entry.text}
                </Text>
              );
            default:
              return null;
          }
        })}
      </Column>

      <Column gap="8" padding="16" style={{ borderTop: "1px solid var(--dm-border-medium)" }}>
        {chat.usage.total > 0 && <ContextBar usage={chat.usage} />}
        <Row as="form" onSubmit={onSend} gap="8" alignItems="end">
          <Textarea
            placeholder={projectId ? "Опишіть завдання для моделі..." : "Спочатку оберіть проєкт"}
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={!projectId || isActive}
          />
          <IconButton
            type="submit"
            icon="arrow-right"
            aria-label="Надіслати завдання"
            disabled={!projectId || isActive || !draft.trim()}
          />
        </Row>
      </Column>
    </Column>
  );
}
