"use client";

import { api } from "@/lib/api";

export function LogoutButton() {
  async function onClick() {
    try {
      await api.post("/api/auth/logout");
    } finally {
      // Even if the call failed, send them to login - a full load also
      // discards any cached server-rendered shell.
      window.location.href = "/login";
    }
  }

  return (
    <button type="button" onClick={onClick} className="hover:text-foreground">
      Sign out
    </button>
  );
}
