# Antara developer guide

## Run it locally

Prerequisites: Docker with the Compose plugin, and Ollama on your host machine for models.

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
docker compose -f deploy/compose/docker-compose.yml up -d --build
```

| Service | URL |
| --- | --- |
| API | http://localhost:8000/docs |
| Model gateway | http://localhost:8088/healthz |
| Temporal UI | http://localhost:8233 |
| Postgres (app) | localhost:5433, user/password/db `antara` |
| Object store (S3) | http://localhost:8333, key `antara` / `antara-secret` |

The gateway forwards to Ollama on the host at `http://host.docker.internal:11434/v1`. Point it elsewhere with `LLM_UPSTREAM_URL` (vLLM on AWS).

## Smoke test

```bash
curl -X POST localhost:8000/documents -H 'X-User-Id: alice' -H 'X-User-Groups: employees' \
  -F groups=employees,contractors -F 'file=@policy.txt;type=text/plain'
curl localhost:8000/documents/<document_id>
curl -X POST localhost:8000/agent/runs -H 'X-User-Id: alice' -H 'X-User-Groups: employees' \
  -H 'content-type: application/json' -d '{"question": "How do contractors get VPN access?"}'
curl localhost:8000/agent/runs/<run_id>
```

Until Keycloak is wired in, the caller is identified by the `X-User-Id` and `X-User-Groups` headers.

## Tests

```bash
pip install -e '.[dev]'
ruff check . && pytest
```

Workflow tests use Temporal's time-skipping test server and need no running stack.

## Frozen contracts

Everything in `common/` is shared by all four slices. Change it only after the team agrees.

| File | Holds |
| --- | --- |
| `common/names.py` | Workflow, activity, signal, query and error names |
| `common/queues.py` | Task queues: `ingest-cpu`, `ingest-gpu`, `agent`, `tools`, `reconciler` |
| `common/models.py` | Every payload passed through Temporal, plus `ChunkResult` and `UserContext` |
| `common/llm.py` | `LLMProvider`, the one client for the model gateway |
| `deploy/sql/001_schema.sql` | App database schema |

## Who owns what

| Folder | Owner | Starting point |
| --- | --- | --- |
| `agent/` | Ranadhir | Agent loop, approvals with escalation, tool registry, effectively-once tool ledger |
| `ingest/` | Prajval | Ingest workflow, embedding fan-out, version publishing, search, deletion. `select_embed_queue` is the CPU/GPU dispatcher to build |
| `deploy/` | Manoj | Compose for local; k3s, KEDA, Temporal Service, vLLM and egress lockdown for AWS go in `deploy/k3s/` |
| `api/`, `gateway/`, `reconciler/` | Vinayak | API admission control (429), gateway limits and chaos flag, reconciler schedule |
