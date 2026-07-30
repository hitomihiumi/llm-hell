import { api } from "./client";

export interface Invite {
  id: string;
  code: string;
  role: "admin" | "user";
  used_by: string | null;
  expires_at: string | null;
}

export const invitesApi = {
  list: () => api.get<Invite[]>("/api/admin/invites"),
  create: (role: "admin" | "user", expires_in_days?: number) =>
    api.post<Invite>("/api/admin/invites", { role, expires_in_days }),
  remove: (id: string) => api.delete<{ ok: boolean }>(`/api/admin/invites/${id}`),
};
