"""
End-to-end test of the MCP surface itself - not just an import check.

This calls the actual functions registered as MCP tools in
app/mcp/server.py directly (the @mcp.tool() decorator preserves them as
plain callables - verified before relying on it), exercising every step
the assignment brief calls "the minimum contract": upload a document,
start a run, inspect status, retrieve pending approvals, approve/reject
items, obtain the final report, and obtain changelog information -
entirely without touching the REST API or the React UI.

Scope note, stated honestly: this proves the tool *implementations* are
correct and drive the same service layer as REST. It does not exercise
the actual MCP stdio/JSON-RPC transport (dict return values are what a
real MCP client would receive after the SDK's own serialization, which
this test does not go through). That transport layer is the SDK's
responsibility, not application logic - what matters here is proving the
tools it exposes actually work end to end, which is what was previously
unverified.
"""

import base64

import pytest

from app.mcp import server as mcp_server
from app.workers.loop import poll_once
from tests.conftest import fixture_bytes

pytestmark = pytest.mark.asyncio


async def _drain(max_iterations: int = 20):
    for _ in range(max_iterations):
        if not await poll_once():
            return


async def test_mcp_tools_are_plain_callables_not_opaque_wrappers():
    """If the @mcp.tool() decorator ever changes to wrap functions
    opaquely, every test in this file would fail for the wrong reason -
    this pins down the assumption explicitly."""
    assert callable(mcp_server.upload_document_text)
    import inspect

    assert inspect.iscoroutinefunction(mcp_server.upload_document_text)


async def test_full_workflow_driven_entirely_through_mcp_tools():
    # 1. Upload a document - base64-encoded, matching the tool's actual signature.
    contract_bytes = fixture_bytes("contract.txt")
    upload_result = await mcp_server.upload_document_text(
        filename="contract.txt", doc_format="txt",
        content_base64=base64.b64encode(contract_bytes).decode(),
    )
    assert "document_id" in upload_result, upload_result
    document_id = upload_result["document_id"]

    # A second document, so a real conflict has something to surface.
    amendment_bytes = fixture_bytes("amendment_1.txt")
    upload_result_2 = await mcp_server.upload_document_text(
        filename="amendment_1.txt", doc_format="txt",
        content_base64=base64.b64encode(amendment_bytes).decode(),
    )
    amendment_id = upload_result_2["document_id"]

    # 2. Define a rule via MCP too.
    rule_result = await mcp_server.define_rule(
        name="Payment terms ceiling", source_type="playbook",
        rule_text="Payment terms must not exceed 30 days.",
    )
    assert "rule_id" in rule_result
    rule_id = rule_result["rule_id"]

    # 3. Start the workflow.
    run_result = await mcp_server.start_run(document_ids=[document_id], rule_ids=[rule_id])
    assert run_result["status"] == "queued"
    run_id = run_result["run_id"]

    # A worker (not MCP - MCP starts runs, a worker process executes
    # them, exactly as REST does) processes the queue.
    await _drain()

    # 4. Inspect status via MCP.
    status_result = await mcp_server.get_run_status(run_id=run_id)
    assert status_result["status"] in ("waiting_approval", "completed")

    # 5. Retrieve pending approvals via MCP.
    pending = await mcp_server.list_pending_approvals(run_id=run_id)
    assert pending["conflicts"] == []  # first-ever contract, nothing to conflict with

    # 6. Approve/reject items via MCP, one at a time (item-by-item).
    for finding in pending["findings"]:
        result = await mcp_server.submit_approval(
            run_id=run_id, item_type="finding", item_id=finding["id"],
            decision="approved", reviewer="mcp-test-reviewer",
        )
        assert result.get("applied") is True, result
    for claim in pending["report_claims"]:
        result = await mcp_server.submit_approval(
            run_id=run_id, item_type="report_claim", item_id=claim["id"],
            decision="approved", reviewer="mcp-test-reviewer",
        )
        assert result.get("applied") is True, result

    final_status = await mcp_server.get_run_status(run_id=run_id)
    assert final_status["status"] == "completed"

    # 7. Obtain the final report via MCP - both the discovery tool and
    #    the direct-lookup tool.
    run_report = await mcp_server.get_run_report(run_id=run_id)
    assert run_report["status"] == "committed"
    assert len(run_report["claims"]) > 0
    assert all(c["status"] == "approved" for c in run_report["claims"])

    report_id = run_report["report_id"]
    report_direct = await mcp_server.get_report(report_id=report_id)
    assert report_direct["status"] == "committed"
    assert report_direct["content"] == run_report["content"]

    # 8. Obtain changelog information via MCP.
    changelog = await mcp_server.get_changelog(limit=50)
    assert len(changelog["entries"]) > 0
    assert any(e["action"] == "report.committed" for e in changelog["entries"])

    # Bonus: cost report, also part of the machine interface.
    cost = await mcp_server.get_cost_report(run_id=run_id)
    assert cost["total_cost_usd"] == 0.0  # fake provider
    assert len(cost["by_node"]) > 0

    # Now prove the SAME commit-gate fix that REST relies on also holds
    # for the MCP path: a second run with a genuine conflict must not
    # let MCP commit it prematurely either.
    run_2 = await mcp_server.start_run(document_ids=[amendment_id], rule_ids=[rule_id])
    await _drain()
    pending_2 = await mcp_server.list_pending_approvals(run_id=run_2["run_id"])
    assert len(pending_2["conflicts"]) == 1

    status_2 = await mcp_server.get_run_status(run_id=run_2["run_id"])
    assert status_2["status"] == "waiting_approval"

    # Deliberately do NOT decide the conflict - assert the run genuinely
    # stays open, entirely via MCP tool calls.
    still_pending = await mcp_server.list_pending_approvals(run_id=run_2["run_id"])
    assert len(still_pending["conflicts"]) == 1
    status_still = await mcp_server.get_run_status(run_id=run_2["run_id"])
    assert status_still["status"] == "waiting_approval"
