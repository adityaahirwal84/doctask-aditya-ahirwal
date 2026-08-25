import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WorkflowStatusBadge, DecisionBadge } from "../components/StatusBadge";
import { LoadingBlock, ErrorBlock, EmptyState } from "../components/Feedback";

describe("WorkflowStatusBadge", () => {
  it("renders a human-readable label for each status", () => {
    render(<WorkflowStatusBadge status="waiting_approval" />);
    expect(screen.getByText("Waiting on approval")).toBeInTheDocument();
  });

  it("renders every workflow status without crashing", () => {
    const statuses = ["queued", "running", "waiting_approval", "retry", "completed", "failed"] as const;
    for (const status of statuses) {
      const { unmount } = render(<WorkflowStatusBadge status={status} />);
      unmount();
    }
  });
});

describe("DecisionBadge", () => {
  it("renders the decision value with underscores replaced", () => {
    render(<DecisionBadge value="evidence_not_found" />);
    expect(screen.getByText("evidence not found")).toBeInTheDocument();
  });
});

describe("Feedback primitives", () => {
  it("LoadingBlock shows its label", () => {
    render(<LoadingBlock label="Loading runs…" />);
    expect(screen.getByText("Loading runs…")).toBeInTheDocument();
  });

  it("ErrorBlock shows the message and fires onRetry when clicked", () => {
    const onRetry = vi.fn();
    render(<ErrorBlock message="Could not reach the API." onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the API.");
    fireEvent.click(screen.getByText("Retry"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("ErrorBlock omits the retry button when no handler is given", () => {
    render(<ErrorBlock message="Gone." />);
    expect(screen.queryByText("Retry")).not.toBeInTheDocument();
  });

  it("EmptyState renders its children", () => {
    render(<EmptyState>Nothing here yet.</EmptyState>);
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
  });
});
