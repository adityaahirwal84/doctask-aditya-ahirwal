import { useParams, Link } from "react-router-dom";
import { endpoints } from "../api/endpoints";
import { usePolling, useAsync } from "../hooks/useAsync";
import { useReviewerName } from "../hooks/useReviewerName";
import { WorkflowStatusBadge } from "../components/StatusBadge";
import { StageVisualization } from "../components/StageVisualization";
import { LoadingBlock, ErrorBlock, EmptyState } from "../components/Feedback";
import { ConflictCard } from "../components/ConflictCard";
import { FindingCard } from "../components/FindingCard";
import { ReportClaimCard } from "../components/ReportClaimCard";
import { SourceReference } from "../components/SourceReference";

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  if (!runId) return null;

  const run = usePolling(() => endpoints.getRun(runId), [runId], 3000);
  const pending = usePolling(() => endpoints.getPendingApprovals(runId), [runId], 3000);
  const report = usePolling(() => endpoints.getRunReport(runId), [runId], 3000);
  const cost = useAsync(() => endpoints.getCost(runId), [runId]);
  const [reviewer, setReviewer] = useReviewerName();

  if (run.loading && !run.data) return <LoadingBlock label="Loading run…" />;
  if (run.error) return <ErrorBlock message={run.error} onRetry={run.refetch} />;
  if (!run.data) return null;

  const refetchAll = () => {
    run.refetch();
    pending.refetch();
    report.refetch();
    cost.refetch();
  };

  const pendingCount =
    (pending.data?.conflicts.length ?? 0) +
    (pending.data?.findings.length ?? 0) +
    (pending.data?.report_claims.length ?? 0);

  return (
    <div className="space-y-8">
      <div>
        <Link to="/" className="font-mono text-xs text-(--color-ink-faint) hover:text-(--color-accent)">
          ← Dashboard
        </Link>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl font-semibold text-(--color-ink)">
              Run <span className="font-mono text-lg text-(--color-ink-faint)">{run.data.id.slice(0, 8)}</span>
            </h1>
            <p className="mt-1 text-sm text-(--color-ink-soft)">
              {run.data.document_ids.length} document{run.data.document_ids.length !== 1 ? "s" : ""} ·{" "}
              {run.data.rule_ids.length} rule{run.data.rule_ids.length !== 1 ? "s" : ""}
            </p>
          </div>
          <WorkflowStatusBadge status={run.data.status} pulse />
        </div>
      </div>

      {run.data.error && (
        <ErrorBlock message={`${run.data.status === "retry" ? "Retrying after error" : "Failed"}: ${run.data.error}`} />
      )}

      <section className="rounded-sm border border-(--color-rule) bg-(--color-paper-raised) p-5">
        <h2 className="mb-4 font-mono text-[11px] uppercase tracking-wide text-(--color-ink-faint)">
          Pipeline stage
        </h2>
        <StageVisualization run={run.data} />
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-display text-base font-semibold text-(--color-ink)">
            Pending approval {pendingCount > 0 && <span className="text-(--color-status-waiting)">({pendingCount})</span>}
          </h2>
          <label className="flex items-center gap-2 text-xs text-(--color-ink-soft)">
            Reviewer
            <input
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              placeholder="your name"
              className="rounded-sm border border-(--color-rule-strong) bg-(--color-paper) px-2 py-1 font-mono text-xs outline-none focus:border-(--color-accent)"
            />
          </label>
        </div>

        {pending.loading && !pending.data && <LoadingBlock label="Loading pending items…" />}
        {pending.error && <ErrorBlock message={pending.error} onRetry={pending.refetch} />}

        {pending.data && pendingCount === 0 && (
          <EmptyState>
            {run.data.status === "waiting_approval"
              ? "Nothing pending right now - the graph is between decisions."
              : "Nothing awaiting review for this run."}
          </EmptyState>
        )}

        {pending.data && pendingCount > 0 && (
          <div className="space-y-3">
            {pending.data.conflicts.map((c) => (
              <ConflictCard key={c.id} runId={runId} conflict={c} reviewer={reviewer} onDecided={refetchAll} />
            ))}
            {pending.data.findings.map((f) => (
              <FindingCard key={f.id} runId={runId} finding={f} reviewer={reviewer} onDecided={refetchAll} />
            ))}
            {pending.data.report_claims.map((c) => (
              <ReportClaimCard key={c.id} runId={runId} claim={c} reviewer={reviewer} onDecided={refetchAll} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 font-display text-base font-semibold text-(--color-ink)">Report</h2>
        {report.loading && !report.data && <LoadingBlock label="Loading report…" />}
        {report.error && <ErrorBlock message={report.error} onRetry={report.refetch} />}
        {!report.loading && !report.data && (
          <EmptyState>No report generated yet for this run.</EmptyState>
        )}
        {report.data && (
          <div className="rounded-sm border border-(--color-rule) bg-(--color-paper) p-5">
            <div className="mb-3 flex items-center gap-2">
              <span className="font-mono text-[11px] uppercase tracking-wide text-(--color-ink-faint)">
                {report.data.status === "committed" ? "Committed" : "Draft"} · v{report.data.version}
              </span>
            </div>
            <ul className="space-y-3">
              {report.data.claims.map((claim) => (
                <li key={claim.id} className="border-t border-(--color-rule) pt-3 first:border-t-0 first:pt-0">
                  <p
                    className={`font-display text-sm ${claim.is_evidence_not_found ? "text-(--color-ink-faint) line-through decoration-(--color-status-retry)" : "text-(--color-ink)"}`}
                  >
                    {claim.claim_text}
                  </p>
                  <div className="mt-1.5 flex items-center gap-2">
                    <SourceReference source={claim.source} />
                    {claim.status !== "pending" && (
                      <span className="font-mono text-[10px] uppercase text-(--color-ink-faint)">
                        {claim.status}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 font-display text-base font-semibold text-(--color-ink)">Cost & latency</h2>
        {cost.loading && <LoadingBlock label="Loading cost report…" />}
        {cost.error && <ErrorBlock message={cost.error} onRetry={cost.refetch} />}
        {cost.data && cost.data.by_node.length === 0 && <EmptyState>No LLM calls recorded yet.</EmptyState>}
        {cost.data && cost.data.by_node.length > 0 && (
          <div className="overflow-hidden rounded-sm border border-(--color-rule)">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-(--color-rule) bg-(--color-paper-raised) font-mono text-[10px] uppercase tracking-wide text-(--color-ink-faint)">
                  <th className="px-4 py-2 font-medium">Stage</th>
                  <th className="px-4 py-2 font-medium">Model</th>
                  <th className="px-4 py-2 font-medium">Tokens</th>
                  <th className="px-4 py-2 font-medium">Cost</th>
                  <th className="px-4 py-2 font-medium">Latency</th>
                </tr>
              </thead>
              <tbody className="rule-list">
                {cost.data.by_node.map((line, i) => (
                  <tr key={i}>
                    <td className="px-4 py-2 font-mono text-xs">{line.node}</td>
                    <td className="px-4 py-2 font-mono text-xs text-(--color-ink-soft)">{line.model}</td>
                    <td className="px-4 py-2 font-mono text-xs text-(--color-ink-soft)">
                      {line.input_tokens + line.output_tokens}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-(--color-ink-soft)">
                      ${line.cost_usd.toFixed(4)}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-(--color-ink-soft)">{line.latency_ms}ms</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-(--color-rule-strong) font-medium">
                  <td className="px-4 py-2 font-mono text-xs" colSpan={2}>
                    Total
                  </td>
                  <td className="px-4 py-2 font-mono text-xs"></td>
                  <td className="px-4 py-2 font-mono text-xs">${cost.data.total_cost_usd.toFixed(4)}</td>
                  <td className="px-4 py-2 font-mono text-xs">{cost.data.total_latency_ms}ms</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
