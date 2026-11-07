# AGENTS.md

Rules for anyone, human or AI coding assistant, changing this repository. Read this before writing code.

## Architecture: logic in the middle, tools at the edges

Each slice (`agent/`, `ingest/`, `reconciler/`, `api/`, `gateway/`) has the same layers:

| Layer | File | May import | Holds |
| --- | --- | --- | --- |
| Contracts | `common/models.py`, `common/names.py`, `common/queues.py` | standard library only | Payloads, names, queue names shared by all slices |
| Ports | `<slice>/ports.py` | contracts | `typing.Protocol` interfaces for every outside dependency: database, object store, model, clock |
| Services | `<slice>/services.py` | contracts, ports | Business rules. No SQL, no HTTP, no SDK calls, no reading settings |
| Adapters | `<slice>/postgres.py`, `common/llm.py`, `common/storage.py` | anything | The only code that talks to Postgres, S3, models or Temporal's API |
| Entry points | `<slice>/activities.py`, `<slice>/workflows.py`, `<slice>/worker.py`, `api/app.py` | everything | Thin wiring: build adapters, call a service, return its result |

Rules that follow from this:

1. A service receives its dependencies as arguments (ports) and settings as plain values. It never constructs an adapter.
2. Swapping Postgres, the object store or the model server means writing a new adapter that satisfies the port. No service changes.
3. Workflows contain orchestration only: which activity runs, on which queue, with which retry policy. Decisions that need data live in services and run inside activities.
4. Workflow code must stay deterministic: no I/O, no clocks, no randomness, no settings that can differ between replays.

## Test-driven development

1. Write or change the test first, watch it fail, then write the code.
2. Every service function has unit tests that use the in-memory fakes in `tests/fakes.py`. Services must stay at 100% line coverage.
3. Every adapter has integration tests in `tests/integration/` that run against a real Postgres (`scripts/integration.sh`).
4. Every workflow has tests on Temporal's time-skipping environment with fake activities.
5. `scripts/check.sh` must pass (ruff, format, all unit tests, coverage gate) before every commit. The pass rate is 100%; a skipped or failing test is never committed.

## Model behaviour

Small models hallucinate. The harness, not the prompt, is the safety net:

1. Tools are offered only for action requests, and only the tool that matches the request type.
2. One side-effecting action per request. The confirmation text comes from the tool's output, never from the model.
3. Every model-facing change runs the eval suite (`python -m evals.run`). A drop in the pass rate blocks the change.
4. When a new hallucination is found, add an eval case that reproduces it before fixing it.

## SOLID, applied

- Single responsibility: one service function per business rule; adapters do I/O only.
- Open/closed: add a tool by adding a `ToolSpec` and a handler, not by editing the agent loop.
- Liskov: any adapter that satisfies a port works in every service and test.
- Interface segregation: ports are small (`TicketStore`, `ToolLedger`), not one giant repository.
- Dependency inversion: services depend on ports; entry points choose adapters.

## Commits

Small commits, one self-contained change each, with a message that says what changed and why. No AI attribution lines.
