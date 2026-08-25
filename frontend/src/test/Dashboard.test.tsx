import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard";
import { endpoints } from "../api/endpoints";
import type { Run } from "../api/types";

vi.mock("../api/endpoints", () => ({
  endpoints: {
    listRuns: vi.fn(),
    listDocuments: vi.fn(),
    listRules: vi.fn(),
  },
}));

function makeRun(overrides: Partial<Run>): Run {
  return {
    id: "11111111-2222-3333-4444-555555555555",
    status: "completed",
    current_node: "finalize",
    retry_count: 0,
    error: null,
    document_ids: ["d1"],
    rule_ids: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(endpoints.listDocuments).mockResolvedValue([]);
  vi.mocked(endpoints.listRules).mockResolvedValue([]);
});

describe("Dashboard", () => {
  it("shows a loading state before runs resolve", async () => {
    vi.mocked(endpoints.listRuns).mockReturnValue(new Promise(() => {})); // never resolves
    render(<Dashboard />, { wrapper: MemoryRouter });
    expect(screen.getByText(/Loading runs/)).toBeInTheDocument();
    // NewRunPanel's own document/rule fetches resolve independently -
    // let them settle so React doesn't warn about an update after the
    // test's assertions, without changing what this test verifies.
    await waitFor(() => expect(endpoints.listDocuments).toHaveBeenCalled());
  });

  it("shows an error state with retry if fetching runs fails", async () => {
    vi.mocked(endpoints.listRuns).mockRejectedValue(new Error("boom"));
    render(<Dashboard />, { wrapper: MemoryRouter });
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("shows an empty state when there are no runs", async () => {
    vi.mocked(endpoints.listRuns).mockResolvedValue([]);
    render(<Dashboard />, { wrapper: MemoryRouter });
    await waitFor(() => expect(screen.getByText(/No runs yet/)).toBeInTheDocument());
  });

  it("renders a row per run with its status and links to the run detail page", async () => {
    vi.mocked(endpoints.listRuns).mockResolvedValue([
      makeRun({ id: "aaaaaaaa-0000-0000-0000-000000000000", status: "waiting_approval" }),
      makeRun({ id: "bbbbbbbb-0000-0000-0000-000000000000", status: "completed" }),
    ]);
    render(<Dashboard />, { wrapper: MemoryRouter });

    await waitFor(() => expect(screen.getByText("aaaaaaaa")).toBeInTheDocument());
    expect(screen.getByText("bbbbbbbb")).toBeInTheDocument();
    expect(screen.getByText("Waiting on approval")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();

    const link = screen.getByText("aaaaaaaa").closest("a");
    expect(link).toHaveAttribute("href", "/runs/aaaaaaaa-0000-0000-0000-000000000000");
  });
});
