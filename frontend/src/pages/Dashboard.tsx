import { Link } from "react-router-dom";
import { endpoints } from "../api/endpoints";
import { usePolling } from "../hooks/useAsync";
import { WorkflowStatusBadge } from "../components/StatusBadge";
import { LoadingBlock, ErrorBlock, EmptyState } from "../components/Feedback";
import { NewRunPanel } from "../components/NewRunPanel";

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function Dashboard() {
  const runs = usePolling(endpoints.listRuns, [], 4000);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="font-display text-2xl font-semibold text-(--color-ink)">Workflow Dashboard</h1>
        <p className="mt-1 text-sm text-(--color-ink-soft)">
          Every ingestion run, its current stage, and whether it needs your review.
        </p>
      </div>

      <NewRunPanel onRunStarted={runs.refetch} />

      <div>
        <h2 className="mb-3 font-display text-base font-semibold text-(--color-ink)">Runs</h2>
        {runs.loading && !runs.data && <LoadingBlock label="Loading runs…" />}
        {runs.error && <ErrorBlock message={runs.error} onRetry={runs.refetch} />}
        {runs.data && runs.data.length === 0 && (
          <EmptyState>No runs yet - start one above once you've uploaded a document.</EmptyState>
        )}
        {runs.data && runs.data.length > 0 && (
          <div className="overflow-hidden rounded-sm border border-(--color-rule)">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-(--color-rule) bg-(--color-paper-raised) font-mono text-[10px] uppercase tracking-wide text-(--color-ink-faint)">
                  <th className="px-4 py-2 font-medium">Run</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Stage</th>
                  <th className="px-4 py-2 font-medium">Documents</th>
                  <th className="px-4 py-2 font-medium">Retries</th>
                  <th className="px-4 py-2 font-medium">Started</th>
                </tr>
              </thead>
              <tbody className="rule-list">
                {runs.data.map((run) => (
                  <tr key={run.id} className="hover:bg-(--color-paper-raised)">
                    <td className="px-4 py-2.5">
                      <Link to={`/runs/${run.id}`} className="font-mono text-xs text-(--color-accent) hover:underline">
                        {run.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5">
                      <WorkflowStatusBadge status={run.status} pulse />
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-(--color-ink-soft)">
                      {run.current_node ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-(--color-ink-soft)">
                      {run.document_ids.length}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-(--color-ink-soft)">{run.retry_count}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-(--color-ink-faint)">
                      {relativeTime(run.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
