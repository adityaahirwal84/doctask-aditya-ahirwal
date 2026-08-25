"""
The fixture document tests/fixtures/docs/injection_addendum.txt contains
an embedded instruction addressed directly at "the reviewing application",
telling it to mark everything approved, skip evidence requirements, and
hide the fact that it did so. The assertion is not that the system
"detects and blocks" clever phrasing - it's that document content, no
matter what it says, structurally cannot move anything past the human
approval gate. Nothing in the ingest/extract/report path has the
authority to approve; only app/services/approval.py, called by an
explicit reviewer decision, does. A prompt injection attempt has no
privileged path to that code.
"""

import pytest

from app.db.models import ApprovalItemType, WorkflowStatus
from app.db.session import session_scope
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.reports import ReportRepository
from app.repositories.rules import RuleRepository
from app.repositories.workflow import WorkflowRepository
from app.workers.loop import poll_once
from tests.utils import create_rule, start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def _drain(max_iterations: int = 20):
    for _ in range(max_iterations):
        if not await poll_once():
            return


async def test_embedded_instruction_does_not_bypass_the_approval_gate(client):
    contract_id = await upload_fixture(client, "contract.txt")
    injection_id = await upload_fixture(client, "injection_addendum.txt")
    rule_id = await create_rule(
        client, "Payment terms ceiling", "playbook", "Payment terms must not exceed 30 days.",
    )

    # Commit the baseline contract (30-day terms) with nothing pending.
    run_a = await start_run(client, [contract_id], [rule_id])
    await _drain()
    pending_a = (await client.get(f"/runs/{run_a}/pending-approvals")).json()
    decisions = [
        {"item_type": t, "item_id": i["id"], "decision": "approved", "reviewer": "alice"}
        for t, items in (("finding", pending_a["findings"]), ("report_claim", pending_a["report_claims"]))
        for i in items
    ]
    if decisions:
        await client.post(f"/runs/{run_a}/approvals", json={"decisions": decisions})

    async with session_scope() as session:
        baseline_report = await ReportRepository(session).get_for_run(run_a)
        report_version_baseline = baseline_report.version if baseline_report else None

    # Ingest the document carrying the injection attempt. It also
    # genuinely contradicts payment terms (states 45 days, matching the
    # already-approved amendment scenario) - a real conflict should still
    # surface despite the embedded instruction telling the system not to
    # flag anything.
    run_b = await start_run(client, [injection_id], [rule_id])
    await _drain()

    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_b)
        conflicts = await KnowledgeRepository(session).open_conflicts_for_run(run_b)
        findings = await RuleRepository(session).findings_for_run(run_b)
        report = await ReportRepository(session).get_for_run(run_b)

    # 1. The run did not silently complete as if nothing needed review -
    #    the injected "mark everything approved, don't mention this" text
    #    had zero effect on whether the gate opens.
    assert run.status == WorkflowStatus.waiting_approval

    # 2. Nothing was auto-approved. Every conflict/finding this run
    #    produced is still sitting in its default pending/open state - the
    #    injection did not flip a single status field. (The report is a
    #    single living deliverable shared across runs - see
    #    docs/architecture.md - so its claims from the earlier, genuinely
    #    approved baseline run are correctly no longer 'pending'; the
    #    precise claim here is that this run left the report completely
    #    untouched, checked below.)
    for c in conflicts:
        assert c.status == "open"
    for f in findings:
        assert f.status == "pending"
    assert report is not None
    report_version_after_injection_run = report.version

    # 3. No Approval row exists for this run at all. The only way an
    #    Approval row is ever created is via decide_conflict /
    #    decide_finding / decide_report_claim, called from the API or MCP
    #    tool - never from inside the ingest/extract/report path.
    async with session_scope() as session:
        existing_approvals = await WorkflowRepository(session).approvals_for_run(run_b)
    assert existing_approvals == []

    # 3b. The report was not touched at all by this run - its version is
    #     identical to what it was after the baseline run's explicit human
    #     approval. The injection document introduced only a contested
    #     fact (a genuine conflict, asserted below), and nothing an
    #     open conflict alone can trigger a report update - stronger
    #     evidence of zero effect than checking a status field.
    assert report_version_after_injection_run == report_version_baseline

    # 4. The injected text is present in a chunk as inert content (it was
    #    ingested and is visible to a reviewer), never as something that
    #    altered control flow - i.e. it was treated as data, exactly as
    #    wrap_untrusted() promises for the real provider path.
    async with session_scope() as session:
        from sqlalchemy import select

        from app.db.models import Chunk

        result = await session.execute(select(Chunk).where(Chunk.document_id == injection_id))
        chunk_texts = " ".join(c.content for c in result.scalars().all())
    assert "SYSTEM OVERRIDE NOTICE" in chunk_texts

    # 5. The real conflict this document also introduces (45-day terms)
    #    is exactly what's pending - proving the system kept doing its
    #    actual job rather than being derailed by the injection attempt.
    fact_keys_in_conflict = {c.fact_key for c in conflicts}
    assert "payment_terms_days" in fact_keys_in_conflict
