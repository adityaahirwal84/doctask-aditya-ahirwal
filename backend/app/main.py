from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import documents, reports, rules, runs
from app.graph.checkpointer import close_checkpointer, open_checkpointer


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_checkpointer()
    yield
    await close_checkpointer()


app = FastAPI(
    title="DocTask - Agentic Document Intelligence",
    description="Vendor contract, amendment, and invoice intelligence with grounded reporting and human approval.",
    version="0.1.0",
    lifespan=lifespan,
)

# Review UI runs on a different origin (Vite dev server / its own container)
# and needs the custom x-api-key header, so this is required, not optional.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(rules.router)
app.include_router(runs.router)
app.include_router(reports.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
