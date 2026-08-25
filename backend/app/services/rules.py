"""
Rule validation.

Rules are free text from the user (a compliance checklist line, a
contract-playbook clause, a style-guide rule). We don't try to pre-parse
them into a rigid schema - the provider's evaluate_rule does the reasoning.
What this service is responsible for is retrieval: finding the *right*
passage to check the rule against, so the LLM (real or fake) is grading
something concrete rather than the whole corpus at once, and so every
finding can point at the exact chunk it came from.

Every check is recorded as a Finding row - passed or not - so there is a
full audit trail of what was checked. But a passed or not-applicable
check isn't a judgment call for a human to make; it's auto-resolved
(status='approved', attributed to the system) rather than sitting in the
approval queue as a fake decision. Only genuine problems - failed checks
and ones without evidence - need a human. That is what makes "a clean
corpus yields an honest report of no findings" true of the pending-review
queue, not just the finding's own verdict field.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Finding, Rule
from app.llm.provider import LLMProvider
from app.repositories.documents import DocumentRepository
from app.repositories.rules import RuleRepository
from app.services.observability import record_cost

_NEEDS_HUMAN_REVIEW = {"failed", "evidence_not_found"}


def _initial_status(verdict: str) -> str:
    return "pending" if verdict in _NEEDS_HUMAN_REVIEW else "approved"


async def _best_matching_chunk(
    session: AsyncSession, provider: LLMProvider, query_text: str, document_ids: list[uuid.UUID],
    run_id: uuid.UUID,
):
    doc_repo = DocumentRepository(session)
    [vector], cost = await provider.embed([query_text])
    await record_cost(session, run_id, cost)
    matches = await doc_repo.similarity_search(vector, limit=1, document_ids=document_ids)
    return matches[0] if matches else None


async def validate_sources_against_rules(
    session: AsyncSession, provider: LLMProvider, rule_ids: list[uuid.UUID],
    document_ids: list[uuid.UUID], run_id: uuid.UUID,
) -> list[Finding]:
    rule_repo = RuleRepository(session)
    rules = await rule_repo.list_by_ids(rule_ids)
    findings: list[Finding] = []

    for rule in rules:
        chunk = await _best_matching_chunk(session, provider, rule.rule_text, document_ids, run_id)
        if chunk is None:
            finding = await rule_repo.add_finding(
                Finding(
                    rule_id=rule.id, run_id=run_id, target_type="source_document",
                    source_chunk_id=None, verdict="evidence_not_found",
                    evidence_text=None, status=_initial_status("evidence_not_found"),
                )
            )
            findings.append(finding)
            continue

        evaluation, cost = await provider.evaluate_rule(rule.rule_text, chunk.content)
        await record_cost(session, run_id, cost)
        finding = await rule_repo.add_finding(
            Finding(
                rule_id=rule.id, run_id=run_id, target_type="source_document",
                source_chunk_id=chunk.id if evaluation.verdict != "evidence_not_found" else None,
                verdict=evaluation.verdict, evidence_text=evaluation.evidence_text,
                status=_initial_status(evaluation.verdict),
            )
        )
        findings.append(finding)

    return findings


async def validate_report_against_rules(
    session: AsyncSession, provider: LLMProvider, rule_ids: list[uuid.UUID],
    report_claims: list[tuple[uuid.UUID, str]], run_id: uuid.UUID,
) -> list[Finding]:
    """Same idea, but the passages being checked are the report's own
    claims rather than the source documents - this is what catches a
    report that drifted from what the sources actually support, or that
    violates a style-guide rule in its own wording."""
    rule_repo = RuleRepository(session)
    rules = await rule_repo.list_by_ids(rule_ids)
    findings: list[Finding] = []

    for rule in rules:
        for claim_id, claim_text in report_claims:
            evaluation, cost = await provider.evaluate_rule(rule.rule_text, claim_text)
            await record_cost(session, run_id, cost)
            finding = await rule_repo.add_finding(
                Finding(
                    rule_id=rule.id, run_id=run_id, target_type="report",
                    source_chunk_id=None, verdict=evaluation.verdict,
                    evidence_text=evaluation.evidence_text or claim_text,
                    status=_initial_status(evaluation.verdict),
                )
            )
            findings.append(finding)

    return findings
