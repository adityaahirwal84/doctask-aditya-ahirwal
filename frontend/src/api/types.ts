// Mirrors backend/app/api/schemas.py. Kept as plain interfaces, one file,
// so a schema change on the backend is a one-file diff to reconcile here.

export type WorkflowStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "retry"
  | "completed"
  | "failed";

export type Decision = "approved" | "rejected";
export type ItemType = "conflict" | "finding" | "report_claim";

export interface Document {
  id: string;
  filename: string;
  format: "pdf" | "docx" | "txt" | "md";
  document_type: string;
  classification_confidence: number | null;
  status: string;
  uploaded_at: string;
}

export interface Rule {
  id: string;
  name: string;
  source_type: string;
  rule_text: string;
}

export interface Run {
  id: string;
  status: WorkflowStatus;
  current_node: string | null;
  retry_count: number;
  error: string | null;
  document_ids: string[];
  rule_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface SourceRef {
  chunk_id: string;
  document_id: string;
  document_filename: string;
  section_ref: string | null;
  snippet: string;
}

export interface KnowledgeItemRef {
  id: string;
  fact_value: string;
  source: SourceRef | null;
}

export interface Conflict {
  id: string;
  fact_key: string;
  item_a: KnowledgeItemRef;
  item_b: KnowledgeItemRef;
  description: string;
  status: "open" | "approved" | "rejected";
}

export type FindingVerdict = "passed" | "failed" | "not_applicable" | "evidence_not_found";

export interface Finding {
  id: string;
  rule_id: string;
  rule_name: string;
  target_type: "source_document" | "report";
  source: SourceRef | null;
  verdict: FindingVerdict;
  evidence_text: string | null;
  status: "pending" | "approved" | "rejected";
}

export interface ReportClaim {
  id: string;
  claim_text: string;
  source: SourceRef | null;
  is_evidence_not_found: boolean;
  status: "pending" | "approved" | "rejected";
}

export interface Report {
  id: string;
  run_id: string;
  version: number;
  status: "draft" | "committed";
  content: string;
  claims: ReportClaim[];
}

export interface PendingApprovals {
  conflicts: Conflict[];
  findings: Finding[];
  report_claims: ReportClaim[];
}

export interface ApprovalDecisionIn {
  item_type: ItemType;
  item_id: string;
  decision: Decision;
  reviewer: string;
  comment?: string;
}

export interface Version {
  id: string;
  entity_type: string;
  entity_id: string;
  version_number: number;
  snapshot: Record<string, unknown>;
  change_reason: string;
  caused_by_document_id: string | null;
  created_at: string;
}

export interface AuditLogEntry {
  id: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  at: string;
}

export interface CostLine {
  node: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
}

export interface CostReport {
  run_id: string;
  total_cost_usd: number;
  total_latency_ms: number;
  by_node: CostLine[];
}
