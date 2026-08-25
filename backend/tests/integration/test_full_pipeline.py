import uuid

import pytest

from app.db.models import WorkflowStatus
from app.workers.loop import poll_once
from tests.utils import create_rule, start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def _run_worker_until(status_in: set[str], max_iterations: int = 30):
    """Drives the real worker loop (claim -> execute -> handle outcome) -
    the same code path a deployed worker process runs - until the queue
    drains or we give up."""
    for _ in range(max_iterations):
        claimed = await poll_once()
        if not claimed:
            break
    return


async def test_upload_and_get_document(client):
    doc_id = await upload_fixture(client, "contract.txt")
    resp = await client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "txt"
    assert body["status"] in ("parsed", "uploaded")


async def test_duplicate_upload_is_rejected(client):
    await upload_fixture(client, "contract.txt")
    from tests.conftest import fixture_bytes

    resp = await client.post(
        "/documents",
        files={"file": ("contract.txt", fixture_bytes("contract.txt"), "text/plain")},
    )
    assert resp.status_code == 409


async def test_unsupported_format_rejected(client):
    resp = await client.post(
        "/documents", files={"file": ("malware.exe", b"not a real doc", "application/octet-stream")},
    )
    assert resp.status_code == 400


async def test_full_pipeline_conflict_approval_and_grounded_report(client):
    contract_id = await upload_fixture(client, "contract.txt")
    amendment_id = await upload_fixture(client, "amendment_1.txt")

    rule_id = await create_rule(
        client, "Payment terms ceiling", "playbook",
        "Payment terms must not exceed 30 days.",
    )

    run_id = await start_run(client, [contract_id], [rule_id])
    await _run_worker_until({"waiting_approval", "completed"})

    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] in ("waiting_approval", "completed")

    pending = (await client.get(f"/runs/{run_id}/pending-approvals")).json()
    # A single contract with no prior knowledge has nothing to conflict
    # with - it should never fabricate a conflict.
    assert pending["conflicts"] == []

    # Findings carry the resolved rule name and source, not just ids -
    # the review UI shows these directly without a second lookup.
    if pending["findings"]:
        finding = pending["findings"][0]
        assert finding["rule_name"] == "Payment terms ceiling"
        assert finding["verdict"] in ("passed", "failed", "not_applicable", "evidence_not_found")

    decisions = [
        {"item_type": "finding", "item_id": f["id"], "decision": "approved", "reviewer": "alice"}
        for f in pending["findings"]
    ] + [
        {"item_type": "report_claim", "item_id": c["id"], "decision": "approved", "reviewer": "alice"}
        for c in pending["report_claims"]
    ]
    if decisions:
        resp = await client.post(f"/runs/{run_id}/approvals", json={"decisions": decisions})
        assert resp.status_code == 200

    # Now ingest the amendment, which genuinely contradicts the payment
    # terms committed above (30 -> 45 days) - this must surface as a
    # conflict, not silently overwrite anything.
    run_id_2 = await start_run(client, [amendment_id], [rule_id])
    await _run_worker_until({"waiting_approval", "completed"})

    run_2 = (await client.get(f"/runs/{run_id_2}")).json()
    assert run_2["status"] == "waiting_approval"

    pending_2 = (await client.get(f"/runs/{run_id_2}/pending-approvals")).json()
    assert len(pending_2["conflicts"]) == 1
    conflict = pending_2["conflicts"][0]
    assert conflict["fact_key"] == "payment_terms_days"
    assert "30" in conflict["description"] and "45" in conflict["description"]

    # The review UI depends on this exact nested shape for traceability -
    # both competing values, each with a resolved source (document
    # filename, section, and the actual passage), not just chunk ids.
    assert conflict["item_a"]["fact_value"] == "30"
    assert conflict["item_a"]["source"]["document_filename"] == "contract.txt"
    assert "30" in conflict["item_a"]["source"]["snippet"]
    assert conflict["item_b"]["fact_value"] == "45"
    assert conflict["item_b"]["source"]["document_filename"] == "amendment_1.txt"
    assert "45" in conflict["item_b"]["source"]["snippet"]

    decisions_2 = [
        {"item_type": "conflict", "item_id": conflict["id"], "decision": "approved", "reviewer": "alice"}
    ]
    resp = await client.post(f"/runs/{run_id_2}/approvals", json={"decisions": decisions_2})
    assert resp.status_code == 200

    # The graph re-interrupts for findings/report claims generated after
    # the conflict resolved - drain those too, item by item.
    for _ in range(5):
        pending_3 = (await client.get(f"/runs/{run_id_2}/pending-approvals")).json()
        more_decisions = [
            {"item_type": "finding", "item_id": f["id"], "decision": "approved", "reviewer": "alice"}
            for f in pending_3["findings"]
        ] + [
            {"item_type": "report_claim", "item_id": c["id"], "decision": "approved", "reviewer": "alice"}
            for c in pending_3["report_claims"]
        ]
        if not more_decisions:
            break
        await client.post(f"/runs/{run_id_2}/approvals", json={"decisions": more_decisions})

    final_run = (await client.get(f"/runs/{run_id_2}")).json()
    assert final_run["status"] == "completed"

    # Version history for payment_terms_days shows two versions: the
    # original (30) and the approved amendment (45), each traceable to the
    # document that caused it.
    from app.services.conflict import _fact_entity_id

    entity_id = _fact_entity_id("payment_terms_days")
    history = (await client.get(f"/versions/knowledge_item/{entity_id}")).json()
    assert len(history) == 2
    assert history[0]["snapshot"]["fact_value"] == "30"
    assert history[1]["snapshot"]["fact_value"] == "45"
    assert str(history[1]["caused_by_document_id"]) == str(amendment_id)

    # Changelog answers what/when/why/which source, per the requirement.
    changelog = (await client.get("/changelog")).json()
    assert any(e["action"] == "conflict.approved" for e in changelog)


async def test_cost_report_reflects_real_llm_calls(client):
    contract_id = await upload_fixture(client, "contract.txt")
    run_id = await start_run(client, [contract_id])
    await _run_worker_until({"waiting_approval", "completed"})

    cost = (await client.get(f"/runs/{run_id}/cost")).json()
    assert cost["run_id"] == str(run_id)
    assert len(cost["by_node"]) > 0
    # Fake provider costs $0 by design, but genuinely records tokens/latency.
    assert cost["total_cost_usd"] == 0.0
    assert any(line["node"] == "classify" for line in cost["by_node"])
    assert any(line["node"] == "extract" for line in cost["by_node"])
