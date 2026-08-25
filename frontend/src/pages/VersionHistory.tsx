import { useMemo, useState } from "react";
import { endpoints } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { LoadingBlock, ErrorBlock, EmptyState } from "../components/Feedback";
import type { Version } from "../api/types";

function formatSnapshot(v: Version): string {
  if (v.entity_type === "knowledge_item") {
    const key = v.snapshot["fact_key"];
    const value = v.snapshot["fact_value"];
    return typeof key === "string" && typeof value === "string" ? `${key.replace(/_/g, " ")} → ${value}` : "—";
  }
  if (v.entity_type === "report") {
    return "Report committed";
  }
  return JSON.stringify(v.snapshot);
}

export function VersionHistory() {
  const recent = useAsync(() => endpoints.getRecentVersions(100), []);
  const docs = useAsync(endpoints.listDocuments, []);
  const [factKeyFilter, setFactKeyFilter] = useState("");

  const docFilenames = useMemo(() => {
    const map = new Map<string, string>();
    docs.data?.forEach((d) => map.set(d.id, d.filename));
    return map;
  }, [docs.data]);

  const filtered = useAsync(
    () => (factKeyFilter.trim() ? endpoints.getVersionsByFactKey(factKeyFilter.trim()) : Promise.resolve(null)),
    [factKeyFilter]
  );

  const rows = factKeyFilter.trim() ? filtered.data : recent.data;
  const loading = factKeyFilter.trim() ? filtered.loading : recent.loading;
  const error = factKeyFilter.trim() ? filtered.error : recent.error;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold text-(--color-ink)">Version History</h1>
        <p className="mt-1 text-sm text-(--color-ink-soft)">
          An immutable, append-only record of every fact and report revision - what changed, when, and which
          document caused it.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <input
          value={factKeyFilter}
          onChange={(e) => setFactKeyFilter(e.target.value)}
          placeholder="Filter by fact key, e.g. payment_terms_days"
          className="w-80 rounded-sm border border-(--color-rule-strong) bg-(--color-paper) px-3 py-1.5 font-mono text-sm outline-none focus:border-(--color-accent)"
        />
        {factKeyFilter && (
          <button
            onClick={() => setFactKeyFilter("")}
            className="font-mono text-xs uppercase tracking-wide text-(--color-ink-faint) hover:text-(--color-accent)"
          >
            Clear
          </button>
        )}
      </div>

      {loading && <LoadingBlock label="Loading version history…" />}
      {error && <ErrorBlock message={error} onRetry={factKeyFilter ? filtered.refetch : recent.refetch} />}
      {rows && rows.length === 0 && <EmptyState>No version history yet.</EmptyState>}

      {rows && rows.length > 0 && (
        <div className="overflow-hidden rounded-sm border border-(--color-rule)">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-(--color-rule) bg-(--color-paper-raised) font-mono text-[10px] uppercase tracking-wide text-(--color-ink-faint)">
                <th className="px-4 py-2 font-medium">Entity</th>
                <th className="px-4 py-2 font-medium">v</th>
                <th className="px-4 py-2 font-medium">Change</th>
                <th className="px-4 py-2 font-medium">Why</th>
                <th className="px-4 py-2 font-medium">Caused by</th>
                <th className="px-4 py-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody className="rule-list">
              {rows.map((v) => (
                <tr key={v.id}>
                  <td className="px-4 py-2 font-mono text-xs uppercase text-(--color-ink-faint)">
                    {v.entity_type.replace(/_/g, " ")}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{v.version_number}</td>
                  <td className="px-4 py-2 font-display text-sm">{formatSnapshot(v)}</td>
                  <td className="px-4 py-2 text-xs text-(--color-ink-soft)">{v.change_reason}</td>
                  <td className="px-4 py-2 font-mono text-xs text-(--color-accent)">
                    {v.caused_by_document_id ? (docFilenames.get(v.caused_by_document_id) ?? v.caused_by_document_id.slice(0, 8)) : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-(--color-ink-faint)">
                    {new Date(v.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
