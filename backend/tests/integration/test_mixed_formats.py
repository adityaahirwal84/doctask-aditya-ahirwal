"""
Task 1 explicitly allows mixed document collections ("It takes in related
documents in mixed formats"). Individual format parsers are unit-tested
in tests/unit/test_parsing.py, but until this file, no test ever started
one WORKFLOW RUN over genuinely different formats together and checked
that ingestion, classification, extraction, and grounding all worked
correctly for each format within that single run.
"""

import pytest

from app.workers.loop import poll_once
from tests.utils import start_run, upload_fixture

pytestmark = pytest.mark.asyncio


async def _drain(max_iterations: int = 20):
    for _ in range(max_iterations):
        if not await poll_once():
            return


async def test_single_run_processes_txt_docx_and_pdf_together(client):
    txt_id = await upload_fixture(client, "contract.txt")
    docx_id = await upload_fixture(client, "addendum.docx")
    pdf_id = await upload_fixture(client, "invoice_2077.pdf")

    # Confirm the formats really are what they claim before the run even
    # starts, so a failure later can't be blamed on a bad fixture.
    for doc_id, fmt in ((txt_id, "txt"), (docx_id, "docx"), (pdf_id, "pdf")):
        doc = (await client.get(f"/documents/{doc_id}")).json()
        assert doc["format"] == fmt

    run_id = await start_run(client, [txt_id, docx_id, pdf_id])
    await _drain()

    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] in ("waiting_approval", "completed"), (
        f"run did not process the mixed-format batch cleanly: status={run['status']}, error={run.get('error')}"
    )
    assert set(run["document_ids"]) == {str(txt_id), str(docx_id), str(pdf_id)}

    # Each document was genuinely parsed by ITS OWN parser, not silently
    # skipped - classification succeeded for all three formats.
    for doc_id in (txt_id, docx_id, pdf_id):
        doc = (await client.get(f"/documents/{doc_id}")).json()
        assert doc["status"] == "parsed", f"{doc['filename']} ({doc['format']}) was not parsed"
        assert doc["document_type"] != "unknown", f"{doc['filename']} ({doc['format']}) failed classification"

    pending = (await client.get(f"/runs/{run_id}/pending-approvals")).json()

    # The addendum's termination notice (90 days) genuinely contradicts
    # the contract's (60 days) - a real cross-format conflict, and
    # stronger proof than a clean run would be: conflict detection
    # correctly traces both sides back to the right document regardless
    # of the two documents being different formats (.txt vs .docx).
    assert len(pending["conflicts"]) == 1
    conflict = pending["conflicts"][0]
    assert conflict["fact_key"] == "termination_notice_days"
    sides = {conflict["item_a"]["source"]["document_filename"], conflict["item_b"]["source"]["document_filename"]}
    assert sides == {"contract.txt", "addendum.docx"}

    # Claims exist and are grounded back to their own correct source
    # document - proving traceability holds across formats, not just
    # within one. (The conflicting fact isn't among these yet - it's
    # correctly held in the conflict above until a human decides.)
    assert len(pending["report_claims"]) > 0
    source_filenames = {c["source"]["document_filename"] for c in pending["report_claims"] if c["source"]}
    assert "contract.txt" in source_filenames
    assert "invoice_2077.pdf" in source_filenames

    # The invoice's own distinctive fact (its number) traces specifically
    # back to the PDF, not to one of the other two documents.
    invoice_claims = [c for c in pending["report_claims"] if "INV-2077" in c["claim_text"]]
    assert len(invoice_claims) == 1
    assert invoice_claims[0]["source"]["document_filename"] == "invoice_2077.pdf"
