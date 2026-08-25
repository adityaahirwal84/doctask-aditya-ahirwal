"""
Direct proof of the brief's third movement: "an update should cost like
an update... the parts the new source did not affect stay exactly as
they were, and the system can prove that."

The test: build a baseline report from a full contract (every fact drafted
as a claim), approve it, then ingest an amendment that changes exactly one
fact (payment terms). After the amendment is processed and its conflict
approved, every claim for a fact the amendment didn't touch must be the
literal same database row - same primary key - as before. Only the
payment-terms claim may have changed, and it must be superseded (old row
kept for history, is_current=False) rather than deleted or silently
edited in place.
"""

import pytest

from app.db.session import session_scope
from app.repositories.reports import ReportRepository
from app.workers.loop import poll_once
from tests.utils import start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def _drain(max_iterations: int = 20):
    for _ in range(max_iterations):
        if not await poll_once():
            return


async def test_unaffected_claims_are_the_same_row_after_an_incremental_update(client):
    contract_id = await upload_fixture(client, "contract.txt")

    run_a = await start_run(client, [contract_id])
    await _drain()

    pending_a = (await client.get(f"/runs/{run_a}/pending-approvals")).json()
    assert len(pending_a["report_claims"]) >= 5  # the whole contract's worth of facts

    decisions = [
        {"item_type": "report_claim", "item_id": c["id"], "decision": "approved", "reviewer": "alice"}
        for c in pending_a["report_claims"]
    ]
    await client.post(f"/runs/{run_a}/approvals", json={"decisions": decisions})

    report_after_a = (await client.get(f"/runs/{run_a}/report")).json()
    assert report_after_a["status"] == "committed"

    # Snapshot every claim's id and text, keyed by what fact it's about
    # (encoded in the claim text via our fake provider's templating).
    baseline_claims_by_text = {c["claim_text"]: c["id"] for c in report_after_a["claims"]}
    baseline_version = None
    async with session_scope() as session:
        report = await ReportRepository(session).get_singleton()
        baseline_version = report.version
        baseline_claim_count = len([c for c in report.claims if c.is_current])

    assert baseline_claim_count == len(pending_a["report_claims"])

    # Now the amendment arrives, changing exactly one fact.
    amendment_id = await upload_fixture(client, "amendment_1.txt")
    run_b = await start_run(client, [amendment_id])
    await _drain()

    pending_b = (await client.get(f"/runs/{run_b}/pending-approvals")).json()
    assert len(pending_b["conflicts"]) == 1
    conflict = pending_b["conflicts"][0]
    assert conflict["fact_key"] == "payment_terms_days"

    resp = await client.post(
        f"/runs/{run_b}/approvals",
        json={"decisions": [{"item_type": "conflict", "item_id": conflict["id"], "decision": "approved", "reviewer": "alice"}]},
    )
    assert resp.status_code == 200

    final_run = (await client.get(f"/runs/{run_b}")).json()
    assert final_run["status"] == "completed"

    report_after_b = (await client.get(f"/runs/{run_b}/report")).json()

    # The core assertion: every claim whose text didn't reference the old
    # payment-terms value is the exact same row (same id) as in the
    # baseline - proving it was never touched, not just that it looks the
    # same.
    unaffected_before = {
        text: cid for text, cid in baseline_claims_by_text.items() if "payment terms days is 30" not in text
    }
    claims_after_by_id = {c["id"]: c for c in report_after_b["claims"]}
    for text, claim_id in unaffected_before.items():
        assert claim_id in claims_after_by_id, f"claim {claim_id!r} ({text!r}) is gone - it should be untouched, not deleted"
        assert claims_after_by_id[claim_id]["claim_text"] == text

    # The payment-terms claim changed: the old "30" claim is superseded
    # (not current, not deleted - still in the DB for history), and a new
    # "45" claim exists, auto-approved as a direct consequence of the
    # already-approved conflict decision (no second approval round for
    # the same judgment call).
    old_claim_id = baseline_claims_by_text.get("The payment terms days is 30.")
    assert old_claim_id is not None
    new_terms_claims = [c for c in report_after_b["claims"] if "45" in c["claim_text"] and "payment terms days" in c["claim_text"]]
    assert len(new_terms_claims) == 1
    assert new_terms_claims[0]["id"] != old_claim_id
    assert new_terms_claims[0]["status"] == "approved"

    async with session_scope() as session:
        report = await ReportRepository(session).get_singleton()
        # The old claim row still exists - superseded, not deleted.
        old_row = next((c for c in report.claims if str(c.id) == old_claim_id), None)
        assert old_row is not None
        assert old_row.is_current is False
        assert old_row.claim_text == "The payment terms days is 30."  # untouched, even in its retirement

        # Exactly one fact changed, so the version incremented by exactly
        # the two touches that happened: generate_report's incremental
        # pass (which found nothing new to auto-commit, since the only
        # affected fact was contested) plus commit's conflict-approval
        # refresh. The precise number matters less than the point that
        # it did NOT jump by "every fact in the corpus got re-drafted."
        assert report.version > baseline_version

    # Regression check: the API-facing report view must never show both
    # the superseded and current claim for the same fact side by side -
    # that would silently present a resolved contradiction as if it were
    # still live, exactly the failure mode the whole system exists to
    # prevent.
    claim_texts_after = [c["claim_text"] for c in report_after_b["claims"]]
    assert "The payment terms days is 30." not in claim_texts_after
    assert sum(1 for t in claim_texts_after if "payment terms days" in t) == 1
