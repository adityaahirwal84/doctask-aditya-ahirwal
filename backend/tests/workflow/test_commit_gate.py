"""
Regression test for a real, confirmed bug: LangGraph resolves an
interrupt() call positionally against whatever Command(resume=...) value
unblocked it - it does not re-evaluate whether the condition that caused
the interrupt still holds. Concretely, the original human_approval node
called interrupt() again on every resume if anything was still pending,
but that second interrupt() call silently resolved and fell through
instead of re-pausing, because LangGraph was replaying it against the
resume value from the *first* interrupt, not treating it as a new one.

The result: a run with two simultaneously pending items (a conflict and a
report claim) would run to completion after only ONE of them was decided,
leaving the other permanently open while the run and report both reported
themselves "completed" / "committed".

The fix moved the loop-until-clear logic out of the node and into a
genuine conditional graph edge (route_after_human_approval in
app/graph/nodes.py) - looping back to human_approval via an edge is a
fresh graph step, not a replay of an old interrupted one, so its
interrupt() call pauses correctly. See docs/architecture.md for the full
writeup and the isolated LangGraph experiments that found the right
pattern before this fix was applied.
"""

import pytest

from app.workers.loop import poll_once
from tests.utils import start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def _drain(max_iterations: int = 20):
    for _ in range(max_iterations):
        if not await poll_once():
            return


async def test_run_does_not_complete_while_a_second_pending_item_remains_undecided(client):
    """A run that produces BOTH an open conflict and a pending report
    claim simultaneously must stay waiting_approval after only one of
    them is decided, and must only reach completed once both are."""
    contract_id = await upload_fixture(client, "contract.txt")
    run_a = await start_run(client, [contract_id])
    await _drain()
    pending_a = (await client.get(f"/runs/{run_a}/pending-approvals")).json()
    baseline_decisions = [
        {"item_type": "report_claim", "item_id": c["id"], "decision": "approved", "reviewer": "alice"}
        for c in pending_a["report_claims"]
    ]
    await client.post(f"/runs/{run_a}/approvals", json={"decisions": baseline_decisions})

    # This document both contradicts the committed payment_terms_days
    # (30 -> 45, a genuine conflict) and introduces a brand-new fact
    # (invoice_number, auto-committed, producing a pending report claim)
    # in the same run - exactly the two-pending-items-at-once scenario
    # the bug required to reproduce.
    gate_doc_id = await upload_fixture(client, "conflict_and_new_fact.txt")
    run_b = await start_run(client, [gate_doc_id])
    await _drain()

    pending_b = (await client.get(f"/runs/{run_b}/pending-approvals")).json()
    assert len(pending_b["conflicts"]) == 1, "test setup: expected exactly one open conflict"
    assert len(pending_b["report_claims"]) == 1, "test setup: expected exactly one pending claim"

    # Decide ONLY the claim. The conflict is deliberately left open.
    claim_id = pending_b["report_claims"][0]["id"]
    resp = await client.post(
        f"/runs/{run_b}/approvals",
        json={"decisions": [{"item_type": "report_claim", "item_id": claim_id, "decision": "approved", "reviewer": "alice"}]},
    )
    assert resp.status_code == 200

    # The core regression assertion: the run must still be waiting, and
    # the conflict must still be open. Before the fix, this run's status
    # was "completed" and the conflict was left permanently unresolved.
    run_after_partial_decision = (await client.get(f"/runs/{run_b}")).json()
    assert run_after_partial_decision["status"] == "waiting_approval", (
        "BUG: the run completed while a conflict was still undecided"
    )

    pending_after_partial = (await client.get(f"/runs/{run_b}/pending-approvals")).json()
    assert len(pending_after_partial["conflicts"]) == 1, (
        "BUG: the still-open conflict vanished from the pending queue without a decision"
    )
    assert pending_after_partial["conflicts"][0]["status"] == "open"

    # Now decide the conflict too, and drain whatever the conflict
    # resolution generates (an auto-approved report claim refresh).
    conflict_id = pending_after_partial["conflicts"][0]["id"]
    await client.post(
        f"/runs/{run_b}/approvals",
        json={"decisions": [{"item_type": "conflict", "item_id": conflict_id, "decision": "approved", "reviewer": "alice"}]},
    )
    for _ in range(5):
        p = (await client.get(f"/runs/{run_b}/pending-approvals")).json()
        more = [
            {"item_type": t, "item_id": i["id"], "decision": "approved", "reviewer": "alice"}
            for t, items in (("finding", p["findings"]), ("report_claim", p["report_claims"]))
            for i in items
        ]
        if not more:
            break
        await client.post(f"/runs/{run_b}/approvals", json={"decisions": more})

    final_run = (await client.get(f"/runs/{run_b}")).json()
    assert final_run["status"] == "completed"

    final_report = (await client.get(f"/runs/{run_b}/report")).json()
    assert final_report["status"] == "committed"
    claim_texts = {c["claim_text"]: c["status"] for c in final_report["claims"]}
    assert claim_texts.get("The payment terms days is 45.") == "approved"
    assert "The payment terms days is 30." not in claim_texts  # superseded, not shown as current
