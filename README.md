# DocTask - Agentic Document Intelligence

An agentic pipeline for vendor contracts, amendments, and invoices: it
ingests mixed-format documents, extracts structured knowledge, detects
contradictions between documents, validates sources and its own report
against user-supplied rules, and produces a grounded report where every
claim traces to a source chunk - gated by human approval, resumable after
a crash, safe to run concurrently, and updated incrementally rather than
rebuilt from scratch every time. A React review interface drives the
whole approval workflow.

Built with AI assistance (Claude), disclosed here per the assignment's
own request. The reasoning behind every non-obvious decision - including
two real bugs found and fixed during development - is in
[`docs/architecture.md`](docs/architecture.md) and inline in the code as
docstrings, not just in this file.

## Domain

Vendor services contracts, their amendments, and the invoices issued
under them. Chosen because it's named directly in the brief, it's easy to
produce honest synthetic fixtures for, and it naturally exercises
contradiction detection (an amendment changing payment terms) and rule
validation (a playbook clause like "payment terms must not exceed 30
days") without contrivance.

Accepted formats: PDF, DOCX, TXT, Markdown. Verified to generalize to a
second, differently-worded contract with different companies, values,
and jurisdiction - not just the original demo documents (see
`tests/integration/test_generalization.py`).

## The five things that don't get cut

1. **Visible, branching steps.** The LangGraph workflow (`app/graph/`)
   loops per document (not per batch) so a crash mid-ingest resumes at
   exactly the right document, and the human-approval node genuinely
   branches on live data - it only interrupts when something is actually
   pending.
2. **Survives being killed.** State lives in Postgres (via LangGraph's own
   checkpointer) rather than in a worker process's memory. This is proven,
   not asserted - `tests/workflow/test_crash_recovery.py` starts a run in
   a real subprocess, sends it `SIGKILL` mid-ingest, and shows a second,
   independent process resumes it without reprocessing the completed
   document.
3. **A human holds the gate.** Every conflict, finding, and report claim
   is approved or rejected individually - in the REST API, the MCP
   server, and the React review UI's item-by-item approval cards
   (`frontend/src/components/{Conflict,Finding,ReportClaim}Card.tsx`).
   Rejecting one item never touches the others, and nothing is deleted,
   only marked.
4. **Fully machine-drivable.** `app/mcp/server.py` exposes the same
   operations as the REST API, over the identical service layer - upload,
   start a run, list pending approvals, submit a decision, read the
   report, read the changelog, read the cost report.
5. **Never bluffs.** Report claims are only kept if their embedding
   similarity to the cited source chunk clears a threshold
   (`app/services/reporting.py`); anything that doesn't becomes an
   explicit `Evidence Not Found` claim, not a dropped or invented one.

## And the one that took the most rework: updates cost like updates

The report is one living deliverable, not a fresh copy per run. When an
amendment changes exactly one fact, only that fact's claim is superseded
and replaced - every other claim in the report is the literal same
database row, unchanged, before and after. This is proven in
`tests/workflow/test_incremental_report.py`, which asserts unaffected
claims keep the same primary key across runs. Getting here required a
real mid-development refactor (see docs/architecture.md) - the first
version regenerated the entire report from scratch on every run, which
technically worked but violated "an update should cost like an update"
outright.

## A strict audit found a real bug - here's what it was

Before calling this done, every requirement in the brief was checked
against the actual code and tests, not the claims in this README, and
graded PASS/PARTIAL/MISSING with the exact file or test as evidence. It
found one serious bug and several real coverage gaps, all now fixed:

- **The commit gate had a genuine bug.** LangGraph resolves an
  `interrupt()` call positionally against whatever `Command(resume=...)`
  value unblocked it - it does not re-evaluate whether the condition
  that caused the interrupt still holds. A run with two pending items
  (say, an open conflict and a pending report claim) would run to
  completion after only one was decided, silently leaving the other
  unresolved forever. Fixed by moving the loop-until-clear logic out of
  the node and into a real conditional graph edge
  (`app/graph/nodes.py:route_after_human_approval`) - looping back via
  an edge is a genuinely new graph step, so its `interrupt()` call pauses
  correctly. Regression test:
  `tests/workflow/test_commit_gate.py`.
- **No MCP end-to-end test existed** - the server file was real, but
  nothing had ever called it. `tests/integration/test_mcp_end_to_end.py`
  now drives the entire workflow - upload, start, status, pending
  approvals, item-by-item decisions, report, changelog, cost - through
  the MCP tool functions alone.
- **The "Evidence Not Found" path was only ever proven not to
  false-positive**, never proven to actually fire.
  `tests/unit/test_evidence_not_found.py` constructs a genuinely
  mismatched source and proves the real grounding mechanism catches it.
- **No test ever ran mixed formats together in one workflow**, and
  building one (`tests/integration/test_mixed_formats.py`) found a real
  `parse_pdf` bug: it chunked by splitting blank lines in flat text
  extraction, which silently collapses into one giant chunk for PDFs
  whose content stream doesn't preserve paragraph gaps - not a contrived
  case, a real limitation of many real-world PDFs. Fixed by chunking on
  PyMuPDF's genuine visual block detection instead.
- **The retry policy was declared but never exercised.**
  `tests/workflow/test_retry.py` found and documented a real, useful
  nuance in the process: LangGraph's default retry filter deliberately
  does not retry `RuntimeError`/`ValueError`/`OSError`-family exceptions
  (treated as bugs, not transient conditions) but does retry
  `ConnectionError` and unlisted types - verified directly against
  LangGraph's source, not assumed.
- **"New documents arrive into a watched location" was actually
  missing** - only explicit upload existed. `app/workers/watch_folder.py`
  is now a real (polling-based) filesystem watcher, tested in
  `tests/integration/test_watch_folder.py`.

Full audit table, methodology, and reproduction steps for the bug are in
`docs/architecture.md`.

```bash
./scripts/bootstrap.sh
curl -H 'x-api-key: dev-local-key' http://localhost:8000/health
```

Opens the API on `http://localhost:8000/docs` and the review UI on
`http://localhost:5173`. `LLM_PROVIDER=fake` by default (see
`.env.example`) - the system runs with no API key. Set
`LLM_PROVIDER=openai` and `LLM_API_KEY` in `.env` for real model calls.

> **Note on this repository's provenance:** the Docker Compose path above
> was written and reviewed carefully but not executed by the assistant
> that built this - the sandbox it ran in has no Docker daemon. Everything
> else was run for real: the backend against a local Postgres 16 +
> pgvector instance (including a genuine killed-subprocess crash-recovery
> test), and the frontend's production build plus its full component
> test suite (32 tests, real DOM rendering via jsdom + React Testing
> Library, no browser binary available in-sandbox either). Please run
> `./scripts/bootstrap.sh` and click through the UI once as the first
> real end-to-end test of both the container path and actual browser
> rendering.

### Running without Docker

```bash
# Backend
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
# Postgres 16 + pgvector running locally, then:
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload &
.venv/bin/python -m app.workers.loop &

# Frontend (separate terminal)
cd frontend
npm install
cp .env.example .env   # points at http://localhost:8000 by default
npm run dev
```

### Running the tests

```bash
# Backend: 49 tests, ~18s, entirely offline (LLM_PROVIDER=fake is forced
# in tests/conftest.py regardless of your shell's .env)
cd backend && .venv/bin/pytest tests/ -v

# Frontend: 32 component tests via Vitest + jsdom
cd frontend && npm test
```

Backend coverage includes unit, integration, and workflow-level crash-
recovery, concurrency, prompt-injection-resistance, incremental-update,
and second-run-generalization tests. Frontend coverage includes every
page's loading/error/empty states and the full item-by-item approve/
reject interaction, with real component rendering (not snapshot tests).

## What was cut, and why

| Cut | Why |
|---|---|
| Celery + Redis | LangGraph's Postgres checkpointer plus a `SELECT ... FOR UPDATE SKIP LOCKED` worker loop gives crash-safety and safe concurrency without a second stateful service. Swapping in Celery later is a change to `app/workers/loop.py` alone - the service layer never touches the queue. |
| JWT / multi-tenant auth | The brief asks for a human approval gate, not multi-user access control. A single shared API key (`app/api/deps.py`) is the honest MVP; building real auth infrastructure nobody asked for was the wrong use of the time budget. |
| OCR | Formats in scope are born-digital PDF/DOCX/TXT/MD. `app/services/parsing.py` is structured so a scanned-document path could be added behind the same interface, but it isn't built. |
| inotify/watchdog for the watched-location watcher | `app/workers/watch_folder.py` polls every 2s instead of using OS-level file-change events. Portable across platforms and trivially testable without event mocking, at the cost of up to 2s latency before a new file is noticed - an explicit tradeoff, not a missing capability. |
| General-purpose rule reasoning in the offline test provider | `FakeLLMProvider` (`app/llm/fake_provider.py`) handles the numeric-threshold and named-value rules this project's fixtures use, deterministically and with no network call. Arbitrary prose rules are `OpenAIProvider`'s job - that's the real implementation; the fake one is honestly scoped to what makes the test suite work offline. |

## Repository layout

```
backend/app/
  api/          FastAPI routers (thin) + serializers that resolve
                traceability (chunk -> document/section/snippet) for
                the review UI
  mcp/          MCP server (thin, same service layer as REST)
  core/         config, auth
  domain/       entities (plain dataclasses, no ORM)
  services/     ingestion, conflict/merge, rules, reporting (incremental),
                approval
  repositories/ SQLAlchemy repository pattern
  graph/        LangGraph state, nodes (incl. the commit-gate fix),
                graph assembly, checkpointer
  llm/          provider interface + fake/openai implementations
  db/           models, session
  workers/      the worker loop and the watched-location watcher
backend/tests/
  unit/         parsing, fake provider (incl. grounding calibration),
                conflict logic, Evidence Not Found, in isolation
  integration/  the real REST API and MCP tools against a real Postgres,
                plus generalization, mixed-format, and watched-location
                tests
  workflow/     crash recovery (real SIGKILL), concurrency, prompt
                injection, retry, commit-gate, and incremental-report-
                update proofs
backend/alembic/  two migrations, both applied and verified
frontend/src/
  api/          typed endpoint functions + response types mirroring
                the backend schemas exactly
  hooks/        loading/error-handling primitives (useAsync, usePolling,
                useMutation) used by every page
  components/   StageVisualization, SourceReference (the traceability
                citation chip), item-by-item approval cards, feedback
                primitives
  pages/        Dashboard, RunDetail, VersionHistory
  test/         32 component tests via Vitest + jsdom + React Testing
                Library - real rendering, not snapshots
docs/architecture.md   the full reasoning behind every structural
                       decision, including every bug found and fixed
                       during development and the strict audit
```
