from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    format: str
    document_type: str
    classification_confidence: float | None
    status: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class RuleIn(BaseModel):
    name: str
    source_type: str  # compliance_checklist | playbook | style_guide
    rule_text: str


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    rule_text: str

    model_config = {"from_attributes": True}


class RunCreateIn(BaseModel):
    document_ids: list[uuid.UUID]
    rule_ids: list[uuid.UUID] = []


class RunOut(BaseModel):
    id: uuid.UUID
    status: str
    current_node: str | None
    retry_count: int
    error: str | None
    document_ids: list[str]
    rule_ids: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceRef(BaseModel):
    """Resolved traceability: not just a chunk id, but what a reviewer
    actually needs to see - which document, which section, and the
    passage itself."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str
    section_ref: str | None
    snippet: str


class KnowledgeItemRef(BaseModel):
    id: uuid.UUID
    fact_value: str
    source: SourceRef | None

    model_config = {"from_attributes": True}


class ConflictOut(BaseModel):
    id: uuid.UUID
    fact_key: str
    item_a: KnowledgeItemRef
    item_b: KnowledgeItemRef
    description: str
    status: str

    model_config = {"from_attributes": True}


class FindingOut(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: str
    target_type: str
    source: SourceRef | None
    verdict: str
    evidence_text: str | None
    status: str

    model_config = {"from_attributes": True}


class ReportClaimOut(BaseModel):
    id: uuid.UUID
    claim_text: str
    source: SourceRef | None
    is_evidence_not_found: bool
    status: str

    model_config = {"from_attributes": True}


class ReportOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    version: int
    status: str
    content: str
    claims: list[ReportClaimOut]

    model_config = {"from_attributes": True}


class PendingApprovalsOut(BaseModel):
    conflicts: list[ConflictOut]
    findings: list[FindingOut]
    report_claims: list[ReportClaimOut]


class ApprovalDecisionIn(BaseModel):
    item_type: str  # conflict | finding | report_claim
    item_id: uuid.UUID
    decision: str  # approved | rejected
    reviewer: str
    comment: str | None = None


class ApprovalBatchIn(BaseModel):
    decisions: list[ApprovalDecisionIn]


class VersionOut(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    version_number: int
    snapshot: dict
    change_reason: str
    caused_by_document_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogOut(BaseModel):
    id: uuid.UUID
    actor: str
    action: str
    entity_type: str
    entity_id: uuid.UUID
    before: dict | None
    after: dict | None
    at: datetime

    model_config = {"from_attributes": True}


class CostLineOut(BaseModel):
    node: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class CostReportOut(BaseModel):
    run_id: uuid.UUID
    total_cost_usd: float
    total_latency_ms: int
    by_node: list[CostLineOut]
