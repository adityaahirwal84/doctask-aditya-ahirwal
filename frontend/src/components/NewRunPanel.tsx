import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { endpoints } from "../api/endpoints";
import { ApiError } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import type { Document, Rule } from "../api/types";
import { LoadingBlock, ErrorBlock } from "./Feedback";

export function NewRunPanel({ onRunStarted }: { onRunStarted: () => void }) {
  const docs = useAsync(endpoints.listDocuments, []);
  const rules = useAsync(endpoints.listRules, []);

  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const [selectedRules, setSelectedRules] = useState<Set<string>>(new Set());
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [ruleName, setRuleName] = useState("");
  const [ruleType, setRuleType] = useState("playbook");
  const [ruleText, setRuleText] = useState("");
  const [ruleError, setRuleError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const navigate = useNavigate();

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, id: string) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setUploadError(null);
    try {
      for (const file of Array.from(files)) {
        const doc = await endpoints.uploadDocument(file);
        setSelectedDocs((prev) => new Set(prev).add(doc.id));
      }
      docs.refetch();
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const handleCreateRule = async () => {
    if (!ruleName.trim() || !ruleText.trim()) return;
    setRuleError(null);
    try {
      const rule = await endpoints.createRule(ruleName.trim(), ruleType, ruleText.trim());
      setSelectedRules((prev) => new Set(prev).add(rule.id));
      setRuleName("");
      setRuleText("");
      setShowRuleForm(false);
      rules.refetch();
    } catch (err) {
      setRuleError(err instanceof ApiError ? err.message : "Could not create rule.");
    }
  };

  const handleStart = async () => {
    if (selectedDocs.size === 0) return;
    setStarting(true);
    setStartError(null);
    try {
      const run = await endpoints.startRun(Array.from(selectedDocs), Array.from(selectedRules));
      onRunStarted();
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setStartError(err instanceof ApiError ? err.message : "Could not start run.");
      setStarting(false);
    }
  };

  return (
    <div className="rounded-sm border border-(--color-rule) bg-(--color-paper-raised) p-5">
      <h2 className="font-display text-base font-semibold text-(--color-ink)">Start a new run</h2>

      {/* Documents */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <label className="font-mono text-[11px] uppercase tracking-wide text-(--color-ink-faint)">
            Documents
          </label>
          <label className="cursor-pointer font-mono text-[11px] uppercase tracking-wide text-(--color-accent) underline-offset-2 hover:underline">
            {uploading ? "Uploading…" : "+ Upload"}
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md"
              className="hidden"
              disabled={uploading}
              onChange={(e) => handleUpload(e.target.files)}
            />
          </label>
        </div>
        {uploadError && <ErrorBlock message={uploadError} />}
        {docs.loading && <LoadingBlock label="Loading documents…" />}
        {docs.error && <ErrorBlock message={docs.error} onRetry={docs.refetch} />}
        {docs.data && docs.data.length === 0 && (
          <p className="text-sm text-(--color-ink-faint)">No documents uploaded yet - upload one above.</p>
        )}
        {docs.data && docs.data.length > 0 && (
          <ul className="max-h-40 overflow-y-auto rounded-sm border border-(--color-rule) bg-(--color-paper)">
            {docs.data.map((doc: Document) => (
              <li key={doc.id} className="flex items-center gap-2 border-b border-(--color-rule) px-3 py-2 last:border-b-0">
                <input
                  type="checkbox"
                  checked={selectedDocs.has(doc.id)}
                  onChange={() => toggle(selectedDocs, setSelectedDocs, doc.id)}
                />
                <span className="font-mono text-sm">{doc.filename}</span>
                <span className="ml-auto font-mono text-[10px] uppercase text-(--color-ink-faint)">
                  {doc.document_type}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Rules */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <label className="font-mono text-[11px] uppercase tracking-wide text-(--color-ink-faint)">
            Rules (optional)
          </label>
          <button
            onClick={() => setShowRuleForm((v) => !v)}
            className="font-mono text-[11px] uppercase tracking-wide text-(--color-accent) hover:underline"
          >
            {showRuleForm ? "Cancel" : "+ New rule"}
          </button>
        </div>

        {showRuleForm && (
          <div className="mb-2 space-y-2 rounded-sm border border-(--color-rule) bg-(--color-paper) p-3">
            <input
              value={ruleName}
              onChange={(e) => setRuleName(e.target.value)}
              placeholder="Rule name, e.g. Payment terms ceiling"
              className="w-full rounded-sm border border-(--color-rule-strong) bg-transparent px-2 py-1.5 text-sm outline-none focus:border-(--color-accent)"
            />
            <select
              value={ruleType}
              onChange={(e) => setRuleType(e.target.value)}
              className="w-full rounded-sm border border-(--color-rule-strong) bg-transparent px-2 py-1.5 text-sm outline-none focus:border-(--color-accent)"
            >
              <option value="playbook">Contract playbook</option>
              <option value="compliance_checklist">Compliance checklist</option>
              <option value="style_guide">Style guide</option>
            </select>
            <textarea
              value={ruleText}
              onChange={(e) => setRuleText(e.target.value)}
              placeholder="Payment terms must not exceed 30 days."
              rows={2}
              className="w-full rounded-sm border border-(--color-rule-strong) bg-transparent px-2 py-1.5 text-sm outline-none focus:border-(--color-accent)"
            />
            {ruleError && <ErrorBlock message={ruleError} />}
            <button
              onClick={handleCreateRule}
              className="rounded-sm px-3 py-1.5 font-mono text-xs uppercase tracking-wide text-white"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              Add rule
            </button>
          </div>
        )}

        {rules.loading && <LoadingBlock label="Loading rules…" />}
        {rules.error && <ErrorBlock message={rules.error} onRetry={rules.refetch} />}
        {rules.data && rules.data.length > 0 && (
          <ul className="max-h-32 overflow-y-auto rounded-sm border border-(--color-rule) bg-(--color-paper)">
            {rules.data.map((rule: Rule) => (
              <li key={rule.id} className="flex items-start gap-2 border-b border-(--color-rule) px-3 py-2 last:border-b-0">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={selectedRules.has(rule.id)}
                  onChange={() => toggle(selectedRules, setSelectedRules, rule.id)}
                />
                <span className="text-sm">
                  <span className="font-medium">{rule.name}</span>
                  <span className="ml-1.5 font-mono text-[10px] uppercase text-(--color-ink-faint)">
                    {rule.source_type.replace(/_/g, " ")}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {startError && (
        <div className="mt-3">
          <ErrorBlock message={startError} />
        </div>
      )}

      <button
        onClick={handleStart}
        disabled={selectedDocs.size === 0 || starting}
        className="mt-4 w-full rounded-sm px-4 py-2 font-mono text-xs uppercase tracking-wide text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
        style={{ backgroundColor: "var(--color-accent)" }}
      >
        {starting ? "Starting…" : `Start run${selectedDocs.size ? ` (${selectedDocs.size} document${selectedDocs.size > 1 ? "s" : ""})` : ""}`}
      </button>
    </div>
  );
}
