import type { WorkflowStatus } from "../api/types";

const WORKFLOW_LABELS: Record<WorkflowStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting_approval: "Waiting on approval",
  retry: "Retrying",
  completed: "Completed",
  failed: "Failed",
};

const WORKFLOW_COLORS: Record<WorkflowStatus, string> = {
  queued: "var(--color-status-queued)",
  running: "var(--color-status-running)",
  waiting_approval: "var(--color-status-waiting)",
  retry: "var(--color-status-retry)",
  completed: "var(--color-status-completed)",
  failed: "var(--color-status-failed)",
};

export function WorkflowStatusBadge({ status, pulse = false }: { status: WorkflowStatus; pulse?: boolean }) {
  const color = WORKFLOW_COLORS[status];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 font-mono text-xs uppercase tracking-wide"
      style={{ color, backgroundColor: `${color}1a`, border: `1px solid ${color}55` }}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${pulse && (status === "running" || status === "retry") ? "animate-pulse-soft" : ""}`}
        style={{ backgroundColor: color }}
      />
      {WORKFLOW_LABELS[status]}
    </span>
  );
}

const DECISION_COLORS: Record<string, string> = {
  approved: "var(--color-decision-approved)",
  rejected: "var(--color-decision-rejected)",
  pending: "var(--color-decision-pending)",
  open: "var(--color-decision-pending)",
  passed: "var(--color-decision-approved)",
  failed: "var(--color-decision-rejected)",
  not_applicable: "var(--color-ink-faint)",
  evidence_not_found: "var(--color-status-retry)",
};

export function DecisionBadge({ value }: { value: string }) {
  const color = DECISION_COLORS[value] ?? "var(--color-ink-faint)";
  return (
    <span
      className="inline-flex items-center rounded-sm px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-wide"
      style={{ color, backgroundColor: `${color}1a` }}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}
