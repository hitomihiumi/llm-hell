import { cookies } from "next/headers";
import Link from "next/link";
import { DICTIONARY } from "@/i18n/dictionary";
import { getLocale } from "@/i18n/getLocale";
import type { RecordOut } from "./types";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

/**
 * Resolves the synthesised `/records/{table}/{pk}` link a Postgres hit
 * carries, since a database row has no natural URL of its own.
 */
export default async function RecordPage({
  params,
}: {
  // params is async in Next 16 - synchronous access was removed.
  params: Promise<{ table: string; pk: string }>;
}) {
  const { table, pk } = await params;
  const copy = DICTIONARY[await getLocale()];
  const cookieStore = await cookies();
  const header = cookieStore
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");

  const response = await fetch(
    `${backendUrl}/api/records/${encodeURIComponent(table)}/${encodeURIComponent(pk)}`,
    { headers: { cookie: header }, cache: "no-store" },
  );

  const backLink = (
    <Link
      href="/search"
      className="group inline-flex items-center gap-3 font-display text-[11px] uppercase tracking-[0.24em] text-white/50 transition-colors duration-300 hover:text-white"
    >
      <svg
        viewBox="0 0 24 12"
        aria-hidden="true"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.25"
        className="h-3 w-6 rotate-180 transition-transform duration-500 ease-out-expo group-hover:-translate-x-1.5"
      >
        <path d="M0 6h21M17 1.5 21.5 6 17 10.5" />
      </svg>
      {copy.record.back}
    </Link>
  );

  if (!response.ok) {
    return (
      <div className="space-y-6">
        {backLink}
        <p className="border-l-2 border-danger bg-danger-soft px-4 py-3 text-sm text-danger">
          {response.status === 404
            ? copy.record.notFound
            : copy.record.unreachable}
        </p>
      </div>
    );
  }

  const record = (await response.json()) as RecordOut;

  return (
    <div className="space-y-8">
      {backLink}

      <header className="border-b border-hairline pb-6">
        <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-white/35">
          {record.table} · id {record.pk}
        </p>
        {/* The record's own title is data, so it keeps its casing. */}
        <h1 className="mt-3 font-display text-3xl font-semibold leading-[1] tracking-tight text-white sm:text-4xl">
          {String(record.fields.title ?? `${record.table} #${record.pk}`)}
        </h1>
      </header>

      <dl className="border-t border-hairline">
        {Object.entries(record.fields).map(([name, value]) => (
          <div
            key={name}
            className="grid gap-2 border-b border-hairline py-5 sm:grid-cols-[12rem_1fr] sm:gap-8"
          >
            <dt className="font-display text-[10px] uppercase tracking-[0.28em] text-white/35">
              {name}
            </dt>
            <dd className="text-sm leading-relaxed break-words whitespace-pre-wrap text-white/75">
              {value === null ? (
                <span className="text-white/25">—</span>
              ) : (
                String(value)
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
