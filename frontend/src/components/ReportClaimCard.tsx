import { useState } from "react";
import { endpoints } from "../api/endpoints";
import { ApiError } from "../api/client";
import type { Decision, ReportClaim } from "../api/types";
import { ApprovalActions } from "./ApprovalActions";
import { SourceReference } from "./SourceReference";

export function ReportClaimCard({
  runId,
  claim,
  reviewer,
  onDecided,
}: {
  runId: string;
  claim: ReportClaim;
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
        { item_type: "report_claim", item_id: claim.id, decision, reviewer },
      ]);
      onDecided();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not submit decision.");
      setPending(false);
    }
  };

  return (
    <div className="rounded-sm border border-(--color-rule) bg-(--color-paper) p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="font-mono text-[11px] uppercase tracking-wide text-(--color-ink-faint)">
            Report claim
          </div>
          <p className="mt-1 font-display text-sm text-(--color-ink)">{claim.claim_text}</p>
          <div className="mt-2">
            <SourceReference source={claim.source} />
          </div>
        </div>
        <ApprovalActions
          pending={pending}
          disabled={!reviewer}
          onApprove={() => decide("approved")}
          onReject={() => decide("rejected")}
        />
      </div>
      {!reviewer && (
        <p className="mt-2 text-xs text-(--color-status-retry)">Enter a reviewer name above to decide.</p>
      )}
      {error && <p className="mt-2 text-xs text-(--color-status-failed)">{error}</p>}
    </div>
  );
}
