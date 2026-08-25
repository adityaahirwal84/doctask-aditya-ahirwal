"""
Domain-level value objects.

These are what the service layer and LangGraph nodes actually pass around.
They are deliberately not SQLAlchemy models: a node in the graph should not
need a DB session to reason about a chunk of text, and keeping these plain
dataclasses is what makes the fake LLM provider and unit tests possible
without a database at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class ExtractedChunk:
    document_id: str
    chunk_index: int
    content: str
    section_ref: str | None = None


@dataclass(frozen=True)
class ExtractedFact:
    """One fact pulled from one chunk, before it becomes a KnowledgeItem row."""

    fact_key: str
    fact_value: str
    source_chunk_index: int
    confidence: float = 1.0


@dataclass(frozen=True)
class ClassificationResult:
    document_type: str
    confidence: float


@dataclass(frozen=True)
class GroundedClaim:
    """A single statement plus whether - and where - it is grounded.

    is_evidence_not_found=True means the system looked and could not
    support the claim; that is the required alternative to inventing one.
    """

    text: str
    source_chunk_id: str | None
    is_evidence_not_found: bool = False


@dataclass(frozen=True)
class RuleEvaluation:
    rule_id: str
    verdict: str  # passed | failed | not_applicable | evidence_not_found
    evidence_text: str | None
    source_chunk_id: str | None


@dataclass
class CostRecord:
    node: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
