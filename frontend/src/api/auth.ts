import { api } from "./client";

export interface User {
  id: string;
  username: string;
  role: "admin" | "user";
}

export const authApi = {
  me: () => api.get<User>("/api/auth/me"),
  login: (username: string, password: string) => api.post<User>("/api/auth/login", { username, password }),
  register: (invite_code: string, username: string, password: string) =>
    api.post<User>("/api/auth/register", { invite_code, username, password }),
  logout: () => api.post<{ ok: boolean }>("/api/auth/logout"),
};
