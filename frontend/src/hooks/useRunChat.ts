import { useCallback, useEffect, useReducer, useRef } from "react";

import type { ContextUsage } from "../api/context";
import { EMPTY_CONTEXT_USAGE } from "../api/context";
import {
  activeEndpointsApi,
  runsApi,
  subscribeToRunEvents,
  type PlanStepData,
  type ReasoningLevel,
  type Run,
  type RunMode,
  type RunSseEvent,
} from "../api/runs";

const SIDE_EFFECT_TOOLS = new Set(["write_file", "run_command", "run_tests"]);

export type ChatEntry =
  | { kind: "user"; text: string }
  | { kind: "plan"; steps: PlanStepData[]; reasoning: string; escalated: boolean }
  | { kind: "step"; index: number; title: string }
  | { kind: "assistant"; text: string; reasoning: string; done: boolean }
  | {
      kind: "tool";
      name: string;
      arguments: unknown;
      status: "pending" | "ok" | "error";
      result?: string;
      needsApproval: boolean;
    }
  | { kind: "error"; message: string }
  | { kind: "status"; text: string };

interface ChatState {
  run: Run | null;
  entries: ChatEntry[];
  usage: ContextUsage;
  awaitingPlanApproval: boolean;
  finished: boolean;
}

type Action =
  | { type: "reset" }
  | { type: "run_created"; run: Run }
  | { type: "sse"; event: RunSseEvent; mode: RunMode };

const initialState: ChatState = {
  run: null,
  entries: [],
  usage: EMPTY_CONTEXT_USAGE,
  awaitingPlanApproval: false,
  finished: false,
};

type AssistantEntry = Extract<ChatEntry, { kind: "assistant" }>;

function lastAssistantEntry(entries: ChatEntry[]): AssistantEntry | undefined {
  const last = entries[entries.length - 1];
  return last && last.kind === "assistant" && !last.done ? last : undefined;
}

function reducer(state: ChatState, action: Action): ChatState {
  if (action.type === "reset") return initialState;
  if (action.type === "run_created") {
    return {
      ...initialState,
      run: action.run,
      entries: [{ kind: "user", text: action.run.task_text }],
    };
  }

  const { event, mode } = action;
  console.log("[useRunChat] reducer sse event", event.type, event.payload);
  const entries = state.entries;
  const open = lastAssistantEntry(entries);

  switch (event.type) {
    case "token": {
      const text = String(event.payload.text ?? "");
      if (open) {
        return { ...state, entries: [...entries.slice(0, -1), { ...open, text: open.text + text }] };
      }
      return { ...state, entries: [...entries, { kind: "assistant", text, reasoning: "", done: false }] };
    }
    case "reasoning": {
      const text = String(event.payload.text ?? "");
      if (open) {
        return {
          ...state,
          entries: [...entries.slice(0, -1), { ...open, reasoning: open.reasoning + text }],
        };
      }
      return {
        ...state,
        entries: [...entries, { kind: "assistant", text: "", reasoning: text, done: false }],
      };
    }
    case "tool_call_start": {
      const name = String(event.payload.tool ?? "");
      const closedEntries = open ? [...entries.slice(0, -1), { ...open, done: true }] : entries;
      return {
        ...state,
        entries: [
          ...closedEntries,
          {
            kind: "tool",
            name,
            arguments: event.payload.arguments,
            status: "pending",
            needsApproval: mode === "stepwise" && SIDE_EFFECT_TOOLS.has(name),
          },
        ],
      };
    }
    case "tool_call_end": {
      const idx = [...entries].reverse().findIndex((e) => e.kind === "tool" && e.status === "pending");
      if (idx === -1) return state;
      const realIdx = entries.length - 1 - idx;
      const updated: ChatEntry = {
        ...(entries[realIdx] as Extract<ChatEntry, { kind: "tool" }>),
        status: event.payload.ok ? "ok" : "error",
        result: String(event.payload.result ?? ""),
        needsApproval: false,
      };
      return { ...state, entries: [...entries.slice(0, realIdx), updated, ...entries.slice(realIdx + 1)] };
    }
    case "step_change": {
      const closedEntries = open ? [...entries.slice(0, -1), { ...open, done: true }] : entries;
      const step = event.payload.step as PlanStepData;
      return {
        ...state,
        entries: [...closedEntries, { kind: "step", index: Number(event.payload.index), title: step.title }],
      };
    }
    case "plan_ready": {
      return {
        ...state,
        entries: [
          ...entries,
          {
            kind: "plan",
            steps: event.payload.steps as PlanStepData[],
            reasoning: String(event.payload.reasoning ?? ""),
            escalated: Boolean(event.payload.escalated),
          },
        ],
        awaitingPlanApproval: mode === "approve_plan" && !state.run?.plan,
      };
    }
    case "context_update":
      return { ...state, usage: event.payload as unknown as ContextUsage };
    case "error":
      return { ...state, entries: [...entries, { kind: "error", message: String(event.payload.message ?? "") }] };
    case "done": {
      const closedEntries = open ? [...entries.slice(0, -1), { ...open, done: true }] : entries;
      const statusText = `Запуск завершено: ${event.payload.status} (${event.payload.stop_reason})`;
      return {
        ...state,
        entries: [...closedEntries, { kind: "status", text: statusText }],
        finished: true,
        awaitingPlanApproval: false,
      };
    }
    default:
      return state;
  }
}

export function useRunChat(projectId: string | null) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const modeRef = useRef<RunMode>("approve_plan");

  useEffect(() => {
    dispatch({ type: "reset" });
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, [projectId]);

  useEffect(() => () => unsubscribeRef.current?.(), []);

  const startRun = useCallback(
    async (opts: {
      taskText: string;
      mode: RunMode;
      reasoningPlanner: ReasoningLevel;
      reasoningExecutor: ReasoningLevel;
    }) => {
      if (!projectId) return;
      const endpoints = await activeEndpointsApi.list();
      const planner = endpoints.find((e) => e.role === "planner");
      const executor = endpoints.find((e) => e.role === "executor");
      if (!planner || !executor) {
        dispatch({
          type: "sse",
          event: { seq: -1, type: "error", payload: { message: "Немає активних ендпоінтів моделей. Зверніться до адміністратора." } },
          mode: opts.mode,
        });
        return;
      }

      const run = await runsApi.create({
        project_id: projectId,
        task_text: opts.taskText,
        mode: opts.mode,
        planner_endpoint_id: planner.id,
        executor_endpoint_id: executor.id,
        reasoning_level_planner: opts.reasoningPlanner,
        reasoning_level_executor: opts.reasoningExecutor,
      });

      modeRef.current = opts.mode;
      dispatch({ type: "run_created", run });

      unsubscribeRef.current?.();
      unsubscribeRef.current = subscribeToRunEvents(run.id, (event) => {
        dispatch({ type: "sse", event, mode: modeRef.current });
      });
    },
    [projectId],
  );

  const stopRun = useCallback(async () => {
    if (state.run) await runsApi.stop(state.run.id);
  }, [state.run]);

  const decidePlan = useCallback(
    async (decision: "approve" | "reject", steps?: PlanStepData[]) => {
      if (!state.run) return;
      await runsApi.decidePlan(state.run.id, decision, steps);
    },
    [state.run],
  );

  const decideStep = useCallback(
    async (decision: "approve" | "reject") => {
      if (!state.run) return;
      await runsApi.decideStep(state.run.id, decision);
    },
    [state.run],
  );

  return { ...state, startRun, stopRun, decidePlan, decideStep };
}
