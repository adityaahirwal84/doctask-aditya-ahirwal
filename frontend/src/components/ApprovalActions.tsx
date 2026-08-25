export function ApprovalActions({
  onApprove,
  onReject,
  pending,
  disabled,
}: {
  onApprove: () => void;
  onReject: () => void;
  pending: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="flex shrink-0 gap-2">
      <button
        onClick={onApprove}
        disabled={pending || disabled}
        className="rounded-sm px-3 py-1.5 font-mono text-xs uppercase tracking-wide text-white transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{ backgroundColor: "var(--color-decision-approved)" }}
      >
        {pending ? "…" : "Approve"}
      </button>
      <button
        onClick={onReject}
        disabled={pending || disabled}
        className="rounded-sm border px-3 py-1.5 font-mono text-xs uppercase tracking-wide transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{ borderColor: "var(--color-decision-rejected)", color: "var(--color-decision-rejected)" }}
      >
        {pending ? "…" : "Reject"}
      </button>
    </div>
  );
}
