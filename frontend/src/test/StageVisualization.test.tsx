import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageVisualization } from "../components/StageVisualization";
import type { Run } from "../api/types";

function makeRun(overrides: Partial<Run>): Run {
  return {
    id: "run-1",
    status: "running",
    current_node: null,
    retry_count: 0,
    error: null,
    document_ids: ["doc-1"],
    rule_ids: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("StageVisualization", () => {
  it("renders all seven pipeline stages", () => {
    render(<StageVisualization run={makeRun({ status: "queued", current_node: null })} />);
    for (const label of [
      "Ingest & Extract",
      "Detect Conflicts",
      "Validate Rules",
      "Generate Report",
      "Human Approval",
      "Commit",
      "Finalize",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("marks stages before current_node as done (checkmark) when waiting on approval", () => {
    render(<StageVisualization run={makeRun({ status: "waiting_approval", current_node: "human_approval" })} />);
    // 5 stages precede human_approval - all should show a checkmark
    expect(screen.getAllByText("✓")).toHaveLength(4);
  });

  it("shows no completed stages for a freshly queued run", () => {
    render(<StageVisualization run={makeRun({ status: "queued", current_node: null })} />);
    expect(screen.queryByText("✓")).not.toBeInTheDocument();
  });

  it("marks every stage done for a completed run", () => {
    render(<StageVisualization run={makeRun({ status: "completed", current_node: "finalize" })} />);
    expect(screen.getAllByText("✓")).toHaveLength(7);
  });
});
