export function LoadingBlock({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-10 text-sm text-(--color-ink-faint)">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-(--color-rule-strong) border-t-(--color-accent)"
        aria-hidden
      />
      {label}
    </div>
  );
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start justify-between gap-4 rounded-sm border px-4 py-3 text-sm"
      style={{
        borderColor: "var(--color-status-failed)",
        backgroundColor: "color-mix(in srgb, var(--color-status-failed) 8%, transparent)",
        color: "var(--color-status-failed)",
      }}
    >
      <span>{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 font-mono text-xs uppercase tracking-wide underline underline-offset-2"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-dashed border-(--color-rule-strong) px-4 py-8 text-center text-sm text-(--color-ink-faint)">
      {children}
    </div>
  );
}
