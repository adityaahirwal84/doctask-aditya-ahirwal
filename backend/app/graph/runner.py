from __future__ import annotations

import logging
import uuid

from langgraph.types import Command

from app.db.models import WorkflowRun, WorkflowStatus
from app.db.session import session_scope
from app.graph.build import compiled_graph
from app.repositories.workflow import WorkflowRepository

logger = logging.getLogger(__name__)


async def create_run(document_ids: list[uuid.UUID], rule_ids: list[uuid.UUID]) -> WorkflowRun:
    """Creates the WorkflowRun row and queues it. Does not execute
    anything - a worker picks it up. This split is what lets POST /runs
    return immediately with a run id a client can poll or drive via MCP,
    per the requirement that the whole workflow be executable
    programmatically end to end."""
    async with session_scope() as session:
        run = WorkflowRun(
            thread_id=str(uuid.uuid4()),
            status=WorkflowStatus.queued,
            document_ids=[str(d) for d in document_ids],
            rule_ids=[str(r) for r in rule_ids],
        )
        run = await WorkflowRepository(session).create(run)
        return run


async def execute_new_run(run_id: uuid.UUID) -> None:
    graph = compiled_graph()
    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        thread_id = run.thread_id
        document_ids = list(run.document_ids)
        rule_ids = list(run.rule_ids)

    initial_state = {
        "run_id": str(run_id),
        "document_ids": document_ids,
        "pending_document_ids": list(document_ids),
        "rule_ids": rule_ids,
        "affected_fact_keys": [],
        "report_id": None,
        "error": None,
    }
    config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke(initial_state, config=config)


async def resume_run(run_id: uuid.UUID) -> None:
    """Resumes a run at exactly the node it stopped at - a crashed node
    re-executes from its start, a satisfied interrupt falls through. See
    app/graph/nodes.py:human_approval for why the resume *value* itself is
    unused - the node re-checks the database, not the Command payload."""
    graph = compiled_graph()
    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        thread_id = run.thread_id
    config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke(Command(resume="continue"), config=config)


async def wake_runs_waiting_on_approval(run_ids: list[uuid.UUID]) -> None:
    """Resumes each given run in turn, tolerating one run's failure
    without abandoning the rest.

    Exists for the report-claim case specifically: the report is one
    system-wide singleton (see app/repositories/reports.py:get_for_run),
    so resolving a claim can clear the human_approval block for every run
    that was waiting on it, not just the run whose endpoint the decision
    came in through. Each of those runs needs its own resume_run() call -
    human_approval already re-checks the database safely and cheaply on
    resume, it just has to actually be invoked - and one run's checkpoint
    being unhealthy, or its thread already gone, must not stop the others
    from waking up. Callers (the approvals endpoints) pass in
    WorkflowRepository.ids_waiting_on_approval(), captured *before* the
    resumes start, so this sweep has a fixed, consistent list to work
    through rather than chasing a set that can change under it."""
    for run_id in run_ids:
        try:
            await resume_run(run_id)
        except Exception:
            logger.exception(
                "Failed to resume run %s while waking runs waiting on a resolved report claim",
                run_id,
            )


async def resume_crashed_run(run_id: uuid.UUID) -> None:
    """For a run that was mid-node (not at a human interrupt) when the
    process died - resumes with no new input, which LangGraph resolves
    against the last saved checkpoint."""
    graph = compiled_graph()
    async with session_scope() as session:
        run = await WorkflowRepository(session).get(run_id)
        thread_id = run.thread_id
    config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke(None, config=config)
