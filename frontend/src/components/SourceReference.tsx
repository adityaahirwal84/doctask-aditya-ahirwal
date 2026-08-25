import { useState } from "react";
import type { SourceRef } from "../api/types";

/**
 * Renders as a small citation mark - [contract.txt · §block 4] - echoing
 * a footnote or a margin note on a reviewed document. Click to expand the
 * actual passage inline, so a reviewer can verify a claim without leaving
 * the page. When source is null, the claim/finding genuinely has no
 * grounding - rendered distinctly, never silently.
 */
export function SourceReference({ source }: { source: SourceRef | null }) {
  const [open, setOpen] = useState(false);

  if (!source) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-wide"
        style={{ color: "var(--color-status-retry)", backgroundColor: "color-mix(in srgb, var(--color-status-retry) 10%, transparent)" }}
      >
        Evidence not found
      </span>
    );
  }

  return (
    <div className="inline-block align-top">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-sm border border-(--color-rule-strong) bg-(--color-paper-raised) px-1.5 py-0.5 font-mono text-[11px] text-(--color-ink-soft) transition-colors hover:border-(--color-accent) hover:text-(--color-accent)"
        aria-expanded={open}
      >
        <span className="text-(--color-accent)">§</span>
        {source.document_filename}
        {source.section_ref ? ` · ${source.section_ref}` : ""}
        <span className="text-(--color-ink-faint)">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="mt-1.5 max-w-md rounded-sm border border-(--color-rule) bg-(--color-paper) p-3 font-display text-sm leading-relaxed text-(--color-ink-soft) shadow-sm">
          <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wide text-(--color-ink-faint)">
            Source passage
          </div>
          “{source.snippet}”
        </div>
      )}
    </div>
  );
}
