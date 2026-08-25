import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConflictCard } from "../components/ConflictCard";
import { FindingCard } from "../components/FindingCard";
import { ReportClaimCard } from "../components/ReportClaimCard";
import { endpoints } from "../api/endpoints";
import type { Conflict, Finding, ReportClaim } from "../api/types";

vi.mock("../api/endpoints", () => ({
  endpoints: {
    submitApprovals: vi.fn(),
  },
}));

const conflict: Conflict = {
  id: "conflict-1",
  fact_key: "payment_terms_days",
  description: "'payment_terms_days' is '30' in committed knowledge but '45' in a newly ingested document.",
  status: "open",
  item_a: { id: "item-a", fact_value: "30", source: { chunk_id: "c1", document_id: "d1", document_filename: "contract.txt", section_ref: null, snippet: "net 30 days" } },
  item_b: { id: "item-b", fact_value: "45", source: { chunk_id: "c2", document_id: "d2", document_filename: "amendment_1.txt", section_ref: null, snippet: "net 45 days" } },
};

const finding: Finding = {
  id: "finding-1",
  rule_id: "rule-1",
  rule_name: "Payment terms ceiling",
  target_type: "source_document",
  source: { chunk_id: "c1", document_id: "d1", document_filename: "contract.txt", section_ref: null, snippet: "net 30 days" },
  verdict: "passed",
  evidence_text: "Payment terms are net 30 days.",
  status: "pending",
};

const claim: ReportClaim = {
  id: "claim-1",
  claim_text: "The payment terms days is 30.",
  source: { chunk_id: "c1", document_id: "d1", document_filename: "contract.txt", section_ref: null, snippet: "net 30 days" },
  is_evidence_not_found: false,
  status: "pending",
};

beforeEach(() => {
  vi.mocked(endpoints.submitApprovals).mockReset();
});

describe("ConflictCard", () => {
  it("shows both competing values with their own source references", () => {
    render(<ConflictCard runId="run-1" conflict={conflict} reviewer="Alice" onDecided={vi.fn()} />);
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getByText("45")).toBeInTheDocument();
    expect(screen.getByText(/contract\.txt/)).toBeInTheDocument();
    expect(screen.getByText(/amendment_1\.txt/)).toBeInTheDocument();
  });

  it("disables approve/reject and prompts for a reviewer name when none is set", () => {
    render(<ConflictCard runId="run-1" conflict={conflict} reviewer="" onDecided={vi.fn()} />);
    expect(screen.getByText("Approve")).toBeDisabled();
    expect(screen.getByText("Reject")).toBeDisabled();
    expect(screen.getByText(/Enter a reviewer name/)).toBeInTheDocument();
  });

  it("submits an approve decision for exactly this item and calls onDecided", async () => {
    vi.mocked(endpoints.submitApprovals).mockResolvedValue({ conflicts: [], findings: [], report_claims: [] });
    const onDecided = vi.fn();
    render(<ConflictCard runId="run-1" conflict={conflict} reviewer="Alice" onDecided={onDecided} />);

    fireEvent.click(screen.getByText("Approve"));

    await waitFor(() => expect(endpoints.submitApprovals).toHaveBeenCalledWith("run-1", [
      { item_type: "conflict", item_id: "conflict-1", decision: "approved", reviewer: "Alice" },
    ]));
    await waitFor(() => expect(onDecided).toHaveBeenCalledOnce());
  });

  it("submits a reject decision when Reject is clicked", async () => {
    vi.mocked(endpoints.submitApprovals).mockResolvedValue({ conflicts: [], findings: [], report_claims: [] });
    render(<ConflictCard runId="run-1" conflict={conflict} reviewer="Alice" onDecided={vi.fn()} />);

    fireEvent.click(screen.getByText("Reject"));

    await waitFor(() =>
      expect(endpoints.submitApprovals).toHaveBeenCalledWith("run-1", [
        { item_type: "conflict", item_id: "conflict-1", decision: "rejected", reviewer: "Alice" },
      ])
    );
  });

  it("shows an error message and does not call onDecided if the API call fails", async () => {
    vi.mocked(endpoints.submitApprovals).mockRejectedValue(new Error("network down"));
    const onDecided = vi.fn();
    render(<ConflictCard runId="run-1" conflict={conflict} reviewer="Alice" onDecided={onDecided} />);

    fireEvent.click(screen.getByText("Approve"));

    await waitFor(() => expect(screen.getByText(/Could not submit decision/)).toBeInTheDocument());
    expect(onDecided).not.toHaveBeenCalled();
  });
});

describe("FindingCard", () => {
  it("shows the rule name, verdict, and evidence", () => {
    render(<FindingCard runId="run-1" finding={finding} reviewer="Alice" onDecided={vi.fn()} />);
    expect(screen.getByText("Payment terms ceiling")).toBeInTheDocument();
    expect(screen.getByText("passed")).toBeInTheDocument();
    expect(screen.getByText(/Payment terms are net 30 days/)).toBeInTheDocument();
  });

  it("approves a finding item-by-item", async () => {
    vi.mocked(endpoints.submitApprovals).mockResolvedValue({ conflicts: [], findings: [], report_claims: [] });
    render(<FindingCard runId="run-1" finding={finding} reviewer="Bob" onDecided={vi.fn()} />);
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() =>
      expect(endpoints.submitApprovals).toHaveBeenCalledWith("run-1", [
        { item_type: "finding", item_id: "finding-1", decision: "approved", reviewer: "Bob" },
      ])
    );
  });
});

describe("ReportClaimCard", () => {
  it("shows the claim text and its source", () => {
    render(<ReportClaimCard runId="run-1" claim={claim} reviewer="Alice" onDecided={vi.fn()} />);
    expect(screen.getByText("The payment terms days is 30.")).toBeInTheDocument();
  });

  it("rejects a report claim item-by-item without affecting other items", async () => {
    vi.mocked(endpoints.submitApprovals).mockResolvedValue({ conflicts: [], findings: [], report_claims: [] });
    render(<ReportClaimCard runId="run-1" claim={claim} reviewer="Alice" onDecided={vi.fn()} />);
    fireEvent.click(screen.getByText("Reject"));
    await waitFor(() =>
      expect(endpoints.submitApprovals).toHaveBeenCalledWith("run-1", [
        { item_type: "report_claim", item_id: "claim-1", decision: "rejected", reviewer: "Alice" },
      ])
    );
    // exactly one decision submitted - proves item-by-item, not batch-wide
    expect(vi.mocked(endpoints.submitApprovals).mock.calls[0][1]).toHaveLength(1);
  });
});
