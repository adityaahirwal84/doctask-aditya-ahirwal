from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.core.config import get_settings
from app.graph import nodes
from app.graph.checkpointer import get_checkpointer
from app.graph.state import GraphState

settings = get_settings()

# Nodes that call an LLM provider or do network/file I/O get real automatic
# retry with exponential backoff via LangGraph's own RetryPolicy - this is
# the system's "Retry" and "Error Recovery" behavior; it isn't a separate
# graph node bolted on for appearances, it's a property of the nodes that
# can actually fail transiently.
_RETRYABLE = RetryPolicy(max_attempts=settings.max_node_retries)


def build_graph():
    builder = StateGraph(GraphState)

    builder.add_node("ingest_one_document", nodes.ingest_one_document, retry_policy=_RETRYABLE)
    builder.add_node("detect_conflicts", nodes.detect_conflicts, retry_policy=_RETRYABLE)
    builder.add_node("validate_rules", nodes.validate_rules, retry_policy=_RETRYABLE)
    builder.add_node("generate_report", nodes.generate_report, retry_policy=_RETRYABLE)
    builder.add_node("human_approval", nodes.human_approval)
    builder.add_node("commit", nodes.commit, retry_policy=_RETRYABLE)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "ingest_one_document")
    builder.add_conditional_edges(
        "ingest_one_document",
        nodes.route_after_ingest,
        {"ingest_one_document": "ingest_one_document", "detect_conflicts": "detect_conflicts"},
    )
    builder.add_edge("detect_conflicts", "validate_rules")
    builder.add_edge("validate_rules", "generate_report")
    builder.add_edge("generate_report", "human_approval")
    builder.add_conditional_edges(
        "human_approval",
        nodes.route_after_human_approval,
        {"human_approval": "human_approval", "commit": "commit"},
    )
    builder.add_edge("commit", "finalize")
    builder.add_edge("finalize", END)

    return builder


def compiled_graph():
    return build_graph().compile(checkpointer=get_checkpointer())
