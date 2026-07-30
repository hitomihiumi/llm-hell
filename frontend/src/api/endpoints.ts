import { api } from "./client";

export type Role = "planner" | "executor";
export type ToolsMode = "native" | "json_protocol";

export interface ModelEndpoint {
  id: string;
  name: string;
  base_url: string;
  has_api_key: boolean;
  model_id: string;
  role: Role;
  ctx_window: number;
  price_per_mtok_in: number;
  price_per_mtok_out: number;
  enabled: boolean;
  tools_mode: ToolsMode;
  reasoning_profile: Record<string, unknown>;
  last_checked_at: string | null;
  last_check_result: EndpointCheckResult | null;
}

export interface LevelProbeResult {
  level: string;
  ok: boolean;
  reasoning_content_present: boolean;
  inline_tags_present: boolean;
  content_sample: string;
  error: string | null;
}

export interface EndpointCheckResult {
  models_ok: boolean;
  models_error: string | null;
  tokenize_ok: boolean;
  tokenize_error: string | null;
  levels: LevelProbeResult[];
  native_tools_supported: boolean;
  native_tools_error: string | null;
}

export interface EndpointCreateInput {
  name: string;
  base_url: string;
  api_key?: string;
  model_id: string;
  role: Role;
  ctx_window: number;
  price_per_mtok_in: number;
  price_per_mtok_out: number;
  tools_mode: ToolsMode;
}

export interface EndpointUpdateInput {
  name?: string;
  base_url?: string;
  api_key?: string;
  model_id?: string;
  role?: Role;
  ctx_window?: number;
  price_per_mtok_in?: number;
  price_per_mtok_out?: number;
  enabled?: boolean;
  tools_mode?: ToolsMode;
  reasoning_profile?: Record<string, unknown>;
}

export const endpointsApi = {
  list: () => api.get<ModelEndpoint[]>("/api/admin/endpoints"),
  create: (body: EndpointCreateInput) => api.post<ModelEndpoint>("/api/admin/endpoints", body),
  update: (id: string, body: EndpointUpdateInput) =>
    api.patch<ModelEndpoint>(`/api/admin/endpoints/${id}`, body),
  remove: (id: string) => api.delete<{ ok: boolean }>(`/api/admin/endpoints/${id}`),
  check: (id: string) => api.post<EndpointCheckResult>(`/api/admin/endpoints/${id}/check`),
};
