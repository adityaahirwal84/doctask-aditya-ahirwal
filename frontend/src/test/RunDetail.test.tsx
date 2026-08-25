import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { RunDetail } from "../pages/RunDetail";
import { endpoints } from "../api/endpoints";
import type { Run, PendingApprovals, Conflict } from "../api/types";

vi.mock("../api/endpoints", () => ({
  endpoints: {
    getRun: vi.fn(),
    getPendingApprovals: vi.fn(),
    getRunReport: vi.fn(),
    getCost: vi.fn(),
    submitApprovals: vi.fn(),
  },
}));

const run: Run = {
  id: "run-1",
  status: "waiting_approval",
  current_node: "human_approval",
  retry_count: 0,
  error: null,
  document_ids: ["d1", "d2"],
  rule_ids: ["r1"],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const conflict: Conflict = {
  id: "conflict-1",
  fact_key: "payment_terms_days",
  description: "'payment_terms_days' is '30' in committed knowledge but '45' in a newly ingested document.",
  status: "open",
  item_a: { id: "item-a", fact_value: "30", source: null },
  item_b: { id: "item-b", fact_value: "45", source: null },
};

const pendingWithConflict: PendingApprovals = { conflicts: [conflict], findings: [], report_claims: [] };
const pendingEmpty: PendingApprovals = { conflicts: [], findings: [], report_claims: [] };

function renderRunDetail() {
  return render(
    <MemoryRouter initialEntries={["/runs/run-1"]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.mocked(endpoints.getRun).mockResolvedValue(run);
  vi.mocked(endpoints.getRunReport).mockResolvedValue(null);
  vi.mocked(endpoints.getCost).mockResolvedValue({ run_id: "run-1", total_cost_usd: 0, total_latency_ms: 0, by_node: [] });
});

describe("RunDetail", () => {
  it("shows the run's workflow status and stage visualization", async () => {
    vi.mocked(endpoints.getPendingApprovals).mockResolvedValue(pendingEmpty);
    renderRunDetail();
    await waitFor(() => expect(screen.getAllByText("Waiting on approval").length).toBeGreaterThan(0));
    expect(screen.getByText("Human Approval")).toBeInTheDocument();
  });

  it("shows a pending conflict awaiting review, with the pending count in the heading", async () => {
    vi.mocked(endpoints.getPendingApprovals).mockResolvedValue(pendingWithConflict);
    renderRunDetail();
    await waitFor(() => expect(screen.getByText(/Pending approval/)).toHaveTextContent("(1)"));
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getByText("45")).toBeInTheDocument();
  });

  it("shows an empty state for pending approvals when nothing is outstanding", async () => {
    vi.mocked(endpoints.getPendingApprovals).mockResolvedValue(pendingEmpty);
    renderRunDetail();
    await waitFor(() => expect(screen.getByText(/Nothing pending right now/)).toBeInTheDocument());
  });

  it("approving the conflict submits the decision and refetches run state", async () => {
    vi.mocked(endpoints.getPendingApprovals).mockResolvedValue(pendingWithConflict);
    vi.mocked(endpoints.submitApprovals).mockResolvedValue(pendingEmpty);
    renderRunDetail();

    await waitFor(() => expect(screen.getByText("Approve")).toBeInTheDocument());

    const reviewerInput = screen.getByPlaceholderText("your name");
    fireEvent.change(reviewerInput, { target: { value: "Alice" } });

    const getRunCallsBefore = vi.mocked(endpoints.getRun).mock.calls.length;
    fireEvent.click(screen.getByText("Approve"));

    await waitFor(() =>
      expect(endpoints.submitApprovals).toHaveBeenCalledWith("run-1", [
        { item_type: "conflict", item_id: "conflict-1", decision: "approved", reviewer: "Alice" },
      ])
    );
    await waitFor(() => expect(vi.mocked(endpoints.getRun).mock.calls.length).toBeGreaterThan(getRunCallsBefore));
  });

  it("shows the run's error message when the run failed", async () => {
    vi.mocked(endpoints.getRun).mockResolvedValue({ ...run, status: "failed", error: "extraction timed out" });
    vi.mocked(endpoints.getPendingApprovals).mockResolvedValue(pendingEmpty);
    renderRunDetail();
    await waitFor(() => expect(screen.getByText(/extraction timed out/)).toBeInTheDocument());
  });
});
