import { useState } from "react";
import { endpoints } from "../api/endpoints";
import { ApiError } from "../api/client";
import type { Conflict, Decision } from "../api/types";
import { ApprovalActions } from "./ApprovalActions";
import { SourceReference } from "./SourceReference";

export function ConflictCard({
  runId,
  conflict,
  reviewer,
  onDecided,
}: {
  runId: string;
  conflict: Conflict;
  reviewer: string;
  onDecided: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const decide = async (decision: Decision) => {
    setPending(true);
    setError(null);
    try {
      await endpoints.submitApprovals(runId, [
        { item_type: "conflict", item_id: conflict.id, decision, reviewer },
      ]);
      onDecided();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not submit decision.");
      setPending(false);
    }
  };

  return (
    <div className="rounded-sm border border-(--color-rule) bg-(--color-paper) p-4">
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <div className="font-mono text-[11px] uppercase tracking-wide text-(--color-status-waiting)">
            Conflict · {conflict.fact_key.replace(/_/g, " ")}
          </div>
          <p className="mt-1 font-display text-sm text-(--color-ink)">{conflict.description}</p>
        </div>
        <ApprovalActions
          pending={pending}
          disabled={!reviewer}
          onApprove={() => decide("approved")}
          onReject={() => decide("rejected")}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-sm bg-(--color-paper-raised) p-3">
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wide text-(--color-ink-faint)">
            Currently committed
          </div>
          <div className="font-display text-sm font-medium">{conflict.item_a.fact_value}</div>
          <div className="mt-2">
            <SourceReference source={conflict.item_a.source} />
          </div>
        </div>
        <div className="rounded-sm bg-(--color-accent-soft) p-3">
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wide text-(--color-accent)">
            Proposed (accepting rejects this)
          </div>
          <div className="font-display text-sm font-medium">{conflict.item_b.fact_value}</div>
          <div className="mt-2">
            <SourceReference source={conflict.item_b.source} />
          </div>
        </div>
      </div>

      {!reviewer && (
        <p className="mt-2 text-xs text-(--color-status-retry)">Enter a reviewer name above to decide.</p>
      )}
      {error && <p className="mt-2 text-xs text-(--color-status-failed)">{error}</p>}
    </div>
  );
}
