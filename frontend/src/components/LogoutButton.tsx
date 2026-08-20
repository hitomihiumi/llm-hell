"use client";

import { useCopy } from "@/i18n/LocaleProvider";
import { api } from "@/lib/api";

export function LogoutButton() {
  const { copy } = useCopy();
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
    <button
      type="button"
      onClick={onClick}
      className="group relative font-display text-[11px] uppercase tracking-[0.24em] text-white/50 transition-colors duration-300 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
    >
      {copy.nav.signOut}
      <span
        aria-hidden="true"
        className="absolute -bottom-2 left-0 h-px w-full origin-left scale-x-0 bg-white transition-transform duration-500 ease-out-expo group-hover:scale-x-100"
      />
    </button>
  );
}
