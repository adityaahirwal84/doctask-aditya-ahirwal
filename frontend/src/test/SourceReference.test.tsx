import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SourceReference } from "../components/SourceReference";
import type { SourceRef } from "../api/types";

const source: SourceRef = {
  chunk_id: "chunk-1",
  document_id: "doc-1",
  document_filename: "contract.txt",
  section_ref: "block 4",
  snippet: "Payment terms are net 30 days from the date of invoice.",
};

describe("SourceReference", () => {
  it("renders a collapsed citation chip with document and section", () => {
    render(<SourceReference source={source} />);
    expect(screen.getByText(/contract\.txt/)).toBeInTheDocument();
    expect(screen.getByText(/block 4/)).toBeInTheDocument();
    // snippet is not shown until expanded
    expect(screen.queryByText(/Payment terms are net 30 days/)).not.toBeInTheDocument();
  });

  it("expands to show the source passage on click, and collapses again", () => {
    render(<SourceReference source={source} />);
    const chip = screen.getByRole("button");
    fireEvent.click(chip);
    expect(screen.getByText(/Payment terms are net 30 days/)).toBeInTheDocument();

    fireEvent.click(chip);
    expect(screen.queryByText(/Payment terms are net 30 days/)).not.toBeInTheDocument();
  });

  it("renders an explicit 'Evidence not found' state when source is null, never a broken/blank reference", () => {
    render(<SourceReference source={null} />);
    expect(screen.getByText("Evidence not found")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
