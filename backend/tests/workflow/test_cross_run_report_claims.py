"""
Regression test for a real, confirmed production bug: a run got stuck in
waiting_approval indefinitely with zero pending items and zero approvals
ever recorded against it (see the investigation of run
3c0c50b2-2bd7-4276-bbe0-1eda950f03f4 in this project's history).

Root cause: the Report - and therefore every ReportClaim - is one
system-wide singleton, not scoped to any one run (see
app/repositories/reports.py:get_for_run, which is explicitly documented
as ignoring its run_id argument). Conflicts and findings ARE correctly
scoped per run. That means two different runs can end up waiting_approval
on the exact same report claim, but only the run whose own
/runs/{run_id}/approvals endpoint received the decision used to get
resumed - any OTHER run blocked on that same claim was left parked at its
interrupt() forever, since nothing else in the system ever re-checks a
waiting_approval run.

The fix (app/graph/runner.py:wake_runs_waiting_on_approval,
app/repositories/workflow.py:ids_waiting_on_approval): whenever a
report_claim decision is applied, every run currently in waiting_approval
gets a resume_run() call, not just the run named in the URL/tool call.
Conflicts and findings keep the narrower single-run resume, since they
can never affect a different run.
"""

import pytest

from app.workers.loop import poll_once
from tests.utils import start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def _drain(max_iterations: int = 20):
    for _ in range(max_iterations):
        if not await poll_once():
            return


async def test_report_claim_decided_via_one_run_wakes_another_run_waiting_on_it(client):
    """Run A: uploads a real document, which - being the first run
    against a fresh test database - seeds the singleton report with
    several new pending claims, and interrupts waiting for them.

    Run B: has zero documents of its own, so it never creates a single
    claim of its own - yet it still reaches human_approval, finds Run A's
    claims still pending (because pending claims are global, not
    run-scoped), and interrupts too. Run B ends up waiting on claims it
    never created, which is the clearest possible demonstration that it's
    the shared state - not anything either run individually did - that
    links them.

    Deciding those claims *only* through Run B's endpoint must wake Run A
    - which never receives any decision through its own endpoint in this
    test - and let it run all the way to completed.
    """
    doc_id = await upload_fixture(client, "contract.txt")
    run_a = await start_run(client, [doc_id])
    await _drain()

    run_a_status = (await client.get(f"/runs/{run_a}")).json()
    assert run_a_status["status"] == "waiting_approval"

    pending_a = (await client.get(f"/runs/{run_a}/pending-approvals")).json()
    assert pending_a["report_claims"], "test setup: expected contract.txt to seed pending report claims"
    assert not pending_a["conflicts"] and not pending_a["findings"], (
        "test setup: expected only report claims to be pending for this simple, single-document case"
    )

    run_b = await start_run(client, [])  # no documents - contributes nothing of its own
    await _drain()

    run_b_status = (await client.get(f"/runs/{run_b}")).json()
    assert run_b_status["status"] == "waiting_approval", (
        "test setup: Run B should be blocked on Run A's still-pending claims, "
        "purely because the report they both see is shared"
    )

    pending_b = (await client.get(f"/runs/{run_b}/pending-approvals")).json()
    assert {c["id"] for c in pending_b["report_claims"]} == {c["id"] for c in pending_a["report_claims"]}, (
        "test setup: Run B should be waiting on exactly the same, shared claims as Run A"
    )

    # Decide every pending claim, but ONLY through Run B's endpoint - Run
    # A's own /runs/{run_a}/approvals is never called anywhere in this test.
    decisions = [
        {"item_type": "report_claim", "item_id": c["id"], "decision": "approved", "reviewer": "alice"}
        for c in pending_b["report_claims"]
    ]
    resp = await client.post(f"/runs/{run_b}/approvals", json={"decisions": decisions})
    assert resp.status_code == 200

    # The actual regression assertion: Run A - which received no decision
    # through its own endpoint - must have been woken up too, and run all
    # the way through commit/finalize.
    final_run_a = (await client.get(f"/runs/{run_a}")).json()
    assert final_run_a["status"] == "completed", (
        "BUG: Run A stayed waiting_approval forever because only Run B - the run whose "
        "endpoint actually received the decision - was resumed"
    )

    final_report = (await client.get(f"/runs/{run_a}/report")).json()
    assert final_report["status"] == "committed"

    # Run B had nothing else pending either, so it should have completed too.
    final_run_b = (await client.get(f"/runs/{run_b}")).json()
    assert final_run_b["status"] == "completed"


async def test_conflict_decision_still_only_resumes_its_own_run(client):
    """Conflicts ARE correctly scoped per run (Conflict.run_id, filtered
    in KnowledgeRepository.open_conflicts_for_run) - unlike report claims,
    resolving one can never affect a different run. This is the
    'unrelated behavior must not change' check for this fix: a second,
    unrelated run sitting in waiting_approval on its own report claims
    must stay exactly where it is when a completely different run's
    conflict gets decided.
    """
    doc_id = await upload_fixture(client, "contract.txt")
    bystander_run = await start_run(client, [doc_id])
    await _drain()
    bystander_status = (await client.get(f"/runs/{bystander_run}")).json()
    assert bystander_status["status"] == "waiting_approval"

    # A second, independent run that produces a genuine conflict against
    # already-committed knowledge (payment_terms_days 30 -> 45) alongside
    # a new fact (invoice_number) - same fixture test_commit_gate.py uses
    # to reliably produce one conflict and one claim in the same run.
    gate_doc_id = await upload_fixture(client, "conflict_and_new_fact.txt")
    conflict_run = await start_run(client, [gate_doc_id])
    await _drain()

    pending_conflict_run = (await client.get(f"/runs/{conflict_run}/pending-approvals")).json()
    assert len(pending_conflict_run["conflicts"]) == 1, "test setup: expected exactly one open conflict"

    conflict_id = pending_conflict_run["conflicts"][0]["id"]
    resp = await client.post(
        f"/runs/{conflict_run}/approvals",
        json={"decisions": [{"item_type": "conflict", "item_id": conflict_id, "decision": "approved", "reviewer": "alice"}]},
    )
    assert resp.status_code == 200

    # The unrelated bystander run must be completely untouched by a
    # conflict decision on a different run.
    bystander_after = (await client.get(f"/runs/{bystander_run}")).json()
    assert bystander_after["status"] == "waiting_approval", (
        "BUG: a conflict decision on an unrelated run should never resume this run"
    )
