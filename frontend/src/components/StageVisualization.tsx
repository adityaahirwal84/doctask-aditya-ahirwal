import type { Run } from "../api/types";

const STAGES: { node: string; label: string }[] = [
  { node: "ingest_one_document", label: "Ingest & Extract" },
  { node: "detect_conflicts", label: "Detect Conflicts" },
  { node: "validate_rules", label: "Validate Rules" },
  { node: "generate_report", label: "Generate Report" },
  { node: "human_approval", label: "Human Approval" },
  { node: "commit", label: "Commit" },
  { node: "finalize", label: "Finalize" },
];

/** Mirrors the fixed node order in backend/app/graph/build.py - the graph
 * doesn't change shape at runtime, so this can be a constant rather than
 * something fetched. */
export function StageVisualization({ run }: { run: Run }) {
  const currentIndex = run.current_node
    ? STAGES.findIndex((s) => s.node === run.current_node)
    : run.status === "queued"
      ? -1
      : STAGES.length - 1;

  const stageState = (index: number): "done" | "current" | "upcoming" => {
    if (run.status === "completed") return "done";
    if (index < currentIndex) return "done";
    if (index === currentIndex) return "current";
    return "upcoming";
  };

  const currentColor =
    run.status === "failed"
      ? "var(--color-status-failed)"
      : run.status === "retry"
        ? "var(--color-status-retry)"
        : run.status === "waiting_approval"
          ? "var(--color-status-waiting)"
          : "var(--color-status-running)";

  return (
    <div className="overflow-x-auto">
      <ol className="flex min-w-max items-center">
        {STAGES.map((stage, index) => {
          const state = stageState(index);
          return (
            <li key={stage.node} className="flex items-center">
              {index > 0 && (
                <div
                  className="h-px w-8"
                  style={{
                    backgroundColor: state === "upcoming" ? "var(--color-rule)" : "var(--color-ink-faint)",
                  }}
                />
              )}
              <div className="flex flex-col items-center gap-1.5 px-2">
                <div
                  className={`flex h-7 w-7 items-center justify-center rounded-full border font-mono text-[11px] ${state === "current" ? "animate-pulse-soft" : ""}`}
                  style={{
                    borderColor: state === "upcoming" ? "var(--color-rule-strong)" : state === "current" ? currentColor : "var(--color-ink-soft)",
                    color: state === "upcoming" ? "var(--color-ink-faint)" : state === "current" ? currentColor : "var(--color-paper)",
                    backgroundColor: state === "done" ? "var(--color-ink-soft)" : state === "current" ? `${currentColor}22` : "transparent",
                  }}
                >
                  {state === "done" ? "✓" : index + 1}
                </div>
                <span
                  className="whitespace-nowrap font-mono text-[11px] uppercase tracking-wide"
                  style={{
                    color: state === "upcoming" ? "var(--color-ink-faint)" : state === "current" ? currentColor : "var(--color-ink-soft)",
                    fontWeight: state === "current" ? 600 : 400,
                  }}
                >
                  {stage.label}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
