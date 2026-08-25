"""
Requirement 5 ("It never bluffs") has two distinct paths in this codebase:
finding-level (app/llm/fake_provider.py::evaluate_rule, already covered by
test_evaluate_rule_evidence_not_found_when_no_number_present) and
report-claim-level (app/services/reporting.py). Every other test that
touches report claims uses well-formed fixtures where grounding correctly
succeeds - which proves the mechanism doesn't false-positive, but never
proves it actually fires when it should. This file closes that gap: it
constructs a knowledge item whose source chunk is genuinely unrelated to
its claimed fact (the kind of mismatch a bad extraction could produce),
runs it through the real generate_grounded_report function - real
FakeLLMProvider embeddings, real cosine similarity, real threshold
comparison - and asserts the claim comes out explicitly marked
"Evidence Not Found", not silently dropped or wrongly asserted.
"""

import uuid

import pytest

from app.db.models import Chunk, Document, DocumentFormat, DocumentStatus, KnowledgeItem, WorkflowRun
from app.db.session import session_scope
from app.llm.fake_provider import FakeLLMProvider
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.reports import ReportRepository
from app.repositories.workflow import WorkflowRepository
from app.services.reporting import generate_grounded_report

pytestmark = pytest.mark.asyncio


async def test_report_claim_becomes_evidence_not_found_when_source_does_not_support_it(client):
    async with session_scope() as session:
        doc = await DocumentRepository(session).create(
            Document(
                filename="mismatched.txt", format=DocumentFormat.txt, content_hash="mismatch-hash",
                storage_path="/tmp/mismatched.txt", status=DocumentStatus.parsed,
            )
        )
        # A source chunk with content that has nothing to do with the fact
        # about to be claimed - simulating a bad extraction rather than a
        # deliberately crafted "impossible to ground" string, so the test
        # exercises the real embedding/threshold machinery honestly.
        unrelated_chunk = Chunk(
            document_id=doc.id, chunk_index=0,
            content="The office is closed on public holidays and reopens the following business day.",
        )
        session.add(unrelated_chunk)
        await session.flush()

        provider = FakeLLMProvider()
        [embedding], _ = await provider.embed([unrelated_chunk.content])
        unrelated_chunk.embedding = embedding
        await session.flush()

        mismatched_item = await KnowledgeRepository(session).add(
            KnowledgeItem(
                document_id=doc.id, source_chunk_id=unrelated_chunk.id,
                fact_key="payment_terms_days", fact_value="30",
                confidence=0.9, is_current=True,
            )
        )
        run = await WorkflowRepository(session).create(WorkflowRun(thread_id=str(uuid.uuid4())))

        report = await generate_grounded_report(session, provider, run.id, affected_fact_keys=None)
        assert report is not None

    async with session_scope() as session:
        full_report = await ReportRepository(session).get_singleton()
        matching_claims = [c for c in full_report.claims if c.fact_key == "payment_terms_days" and c.is_current]
        assert len(matching_claims) == 1
        claim = matching_claims[0]

        # The actual, literal proof: the claim is explicitly marked
        # Evidence Not Found - not dropped (still a row, still visible to
        # a reviewer), not silently left claiming "30" without saying so.
        assert claim.is_evidence_not_found is True
        assert "Evidence Not Found" in claim.claim_text
        assert claim.source_chunk_id is None
        assert claim.status == "pending"


async def test_report_api_surfaces_evidence_not_found_claims_distinctly(client):
    """Same scenario, checked through the actual REST response shape the
    review UI consumes - source is null and is_evidence_not_found is
    true, matching what frontend/src/components/SourceReference.tsx
    renders as 'Evidence not found' rather than a broken citation."""
    async with session_scope() as session:
        doc = await DocumentRepository(session).create(
            Document(
                filename="mismatched2.txt", format=DocumentFormat.txt, content_hash="mismatch-hash-2",
                storage_path="/tmp/mismatched2.txt", status=DocumentStatus.parsed,
            )
        )
        unrelated_chunk = Chunk(
            document_id=doc.id, chunk_index=0,
            content="Parking validation is available at the front desk for visitors.",
        )
        session.add(unrelated_chunk)
        await session.flush()

        provider = FakeLLMProvider()
        [embedding], _ = await provider.embed([unrelated_chunk.content])
        unrelated_chunk.embedding = embedding
        await session.flush()

        await KnowledgeRepository(session).add(
            KnowledgeItem(
                document_id=doc.id, source_chunk_id=unrelated_chunk.id,
                fact_key="governing_law", fact_value="Nevada",
                confidence=0.9, is_current=True,
            )
        )
        run = await WorkflowRepository(session).create(WorkflowRun(thread_id=str(uuid.uuid4())))
        await generate_grounded_report(session, provider, run.id, affected_fact_keys=None)
        run_id = run.id

    resp = await client.get(f"/runs/{run_id}/report")
    body = resp.json()
    assert body is not None
    matching = [c for c in body["claims"] if "governing law" in c["claim_text"] or "Nevada" in c["claim_text"]]
    assert len(matching) == 1
    assert matching[0]["is_evidence_not_found"] is True
    assert matching[0]["source"] is None
