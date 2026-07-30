import { api } from "./client";

export type RunMode = "autopilot" | "approve_plan" | "stepwise";
export type ReasoningLevel = "off" | "low" | "medium" | "high";

export interface PlanStepData {
  id: string;
  title: string;
  intent: string;
  files: string[];
  done_when: string;
}

export interface Run {
  id: string;
  project_id: string;
  task_text: string;
  mode: RunMode;
  status: string;
  plan: PlanStepData[] | null;
  current_step_index: number;
  tokens_prompt: number;
  tokens_completion: number;
  tokens_reasoning: number;
  cost_estimate_usd: number;
  stop_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface ActiveEndpoint {
  id: string;
  name: string;
  role: "planner" | "executor";
  ctx_window: number;
}

export const runsApi = {
  create: (body: {
    project_id: string;
    task_text: string;
    mode: RunMode;
    planner_endpoint_id: string;
    executor_endpoint_id: string;
    reasoning_level_planner: ReasoningLevel;
    reasoning_level_executor: ReasoningLevel;
  }) => api.post<Run>("/api/runs", body),
  get: (id: string) => api.get<Run>(`/api/runs/${id}`),
  list: (projectId: string) => api.get<Run[]>(`/api/runs?project_id=${projectId}`),
  stop: (id: string) => api.post<{ ok: boolean }>(`/api/runs/${id}/stop`),
  decidePlan: (id: string, decision: "approve" | "reject", steps?: PlanStepData[]) =>
    api.post<{ ok: boolean }>(`/api/runs/${id}/plan/decision`, { decision, steps }),
  decideStep: (id: string, decision: "approve" | "reject") =>
    api.post<{ ok: boolean }>(`/api/runs/${id}/step/decision`, { decision }),
};

export const activeEndpointsApi = {
  list: () => api.get<ActiveEndpoint[]>("/api/endpoints"),
};

export interface RunSseEvent {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
}

/** Native `EventSource` covers auth cookies + auto-reconnect on its own;
 * a hand-rolled fetch-stream reader would just re-implement both worse. */
export function subscribeToRunEvents(runId: string, onEvent: (event: RunSseEvent) => void): () => void {
  const source = new EventSource(`/api/runs/${runId}/events`, { withCredentials: true });

  const types = [
    "token",
    "reasoning",
    "tool_call_start",
    "tool_call_end",
    "step_change",
    "plan_ready",
    "context_update",
    "compaction",
    "usage",
    "error",
    "done",
  ];

  for (const type of types) {
    source.addEventListener(type, (e) => {
      const messageEvent = e as MessageEvent<string>;
      onEvent({
        seq: messageEvent.lastEventId ? Number(messageEvent.lastEventId) : -1,
        type,
        payload: JSON.parse(messageEvent.data),
      });
    });
  }

  return () => source.close();
}
