import { cookies } from "next/headers";
import Link from "next/link";
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
  const cookieStore = await cookies();
  const header = cookieStore
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");

  const response = await fetch(
    `${backendUrl}/api/records/${encodeURIComponent(table)}/${encodeURIComponent(pk)}`,
    { headers: { cookie: header }, cache: "no-store" },
  );

  if (!response.ok) {
    return (
      <div className="space-y-3">
        <Link href="/search" className="text-sm text-accent hover:underline">
          ← Back to search
        </Link>
        <p className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {response.status === 404
            ? "That record does not exist, or its table is not searchable."
            : "The knowledge base could not be reached."}
        </p>
      </div>
    );
  }

  const record = (await response.json()) as RecordOut;

  return (
    <div className="space-y-4">
      <Link href="/search" className="text-sm text-accent hover:underline">
        ← Back to search
      </Link>

      <header>
        <h1 className="text-lg font-semibold">
          {String(record.fields.title ?? `${record.table} #${record.pk}`)}
        </h1>
        <p className="text-xs text-muted">
          {record.table} · id {record.pk}
        </p>
      </header>

      <dl className="divide-y divide-border rounded-lg border border-border bg-surface">
        {Object.entries(record.fields).map(([name, value]) => (
          <div key={name} className="grid grid-cols-[10rem_1fr] gap-4 px-4 py-3 text-sm">
            <dt className="text-muted">{name}</dt>
            <dd className="whitespace-pre-wrap break-words">
              {value === null ? <span className="text-muted">—</span> : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
