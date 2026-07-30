import { api } from "./client";

export interface RatingInput {
  thumbs?: boolean | null;
  plan_score?: number | null;
  code_score?: number | null;
  instruction_score?: number | null;
  comment?: string | null;
}

export interface Rating extends RatingInput {
  id: string;
  run_id: string;
  user_id: string;
  created_at: string;
}

export const ratingsApi = {
  rate: (runId: string, body: RatingInput) => api.post<Rating>(`/api/runs/${runId}/ratings`, body),
  list: (runId: string) => api.get<Rating[]>(`/api/runs/${runId}/ratings`),
};
