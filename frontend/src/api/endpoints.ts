import { api } from "./client";
import type {
  ApprovalDecisionIn,
  AuditLogEntry,
  CostReport,
  Document,
  PendingApprovals,
  Report,
  Rule,
  Run,
  Version,
} from "./types";

export const endpoints = {
  listDocuments: () => api.get<Document[]>("/documents"),
  getDocument: (id: string) => api.get<Document>(`/documents/${id}`),
  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.postForm<Document>("/documents", form);
  },

  listRules: () => api.get<Rule[]>("/rules"),
  createRule: (name: string, source_type: string, rule_text: string) =>
    api.post<Rule>("/rules", { name, source_type, rule_text }),

  listRuns: () => api.get<Run[]>("/runs"),
  getRun: (id: string) => api.get<Run>(`/runs/${id}`),
  startRun: (document_ids: string[], rule_ids: string[]) =>
    api.post<Run>("/runs", { document_ids, rule_ids }),
  getRunReport: (id: string) => api.get<Report | null>(`/runs/${id}/report`),
  getPendingApprovals: (id: string) => api.get<PendingApprovals>(`/runs/${id}/pending-approvals`),
  submitApprovals: (id: string, decisions: ApprovalDecisionIn[]) =>
    api.post<PendingApprovals>(`/runs/${id}/approvals`, { decisions }),
  getCost: (id: string) => api.get<CostReport>(`/runs/${id}/cost`),

  getReport: (id: string) => api.get<Report>(`/reports/${id}`),

  getRecentVersions: (limit = 50) => api.get<Version[]>(`/versions/recent?limit=${limit}`),
  getVersionsByFactKey: (factKey: string) =>
    api.get<Version[]>(`/versions/by-fact-key/${encodeURIComponent(factKey)}`),

  getChangelog: (limit = 100) => api.get<AuditLogEntry[]>(`/changelog?limit=${limit}`),
};
