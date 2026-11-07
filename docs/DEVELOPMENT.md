# Antara developer guide

## Run it locally

Prerequisites: Docker with the Compose plugin, and a model server for chat and embeddings. Ollama on the host works:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
docker compose -f deploy/compose/docker-compose.yml up -d --build
```

Add `--profile auth` to also start Keycloak, and set `AUTH_MODE=oidc` to make the API require its tokens.

| Service | URL |
| --- | --- |
| Web app | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Model gateway | http://localhost:8088/healthz |
| Temporal UI | http://localhost:8233 |
| Grafana (Antara overview dashboard) | http://localhost:3000 |
| Jaeger traces | http://localhost:16686 |
| Prometheus | http://localhost:9090 |
| Keycloak (auth profile) | http://localhost:8180, users alice, bob, carol, dave with password `antara-dev` |
| Postgres (app) | localhost:5433, user/password/db `antara` |
| Object store (S3) | http://localhost:8333, key `antara` / `antara-secret` |

The gateway forwards to Ollama on the host at `http://host.docker.internal:11434/v1`. Point it elsewhere with `LLM_UPSTREAM_URL` and `EMBED_UPSTREAM_URL`.

The schema files in `deploy/sql` only run on an empty database. After pulling new schema files, reset the local data with `docker compose -f deploy/compose/docker-compose.yml down -v`.

## Demo identities

In header mode the web app has an identity picker, and API calls send `X-User-Id` and `X-User-Groups`.

| User | Groups | Can |
| --- | --- | --- |
| alice | employees | Ask, upload, request access |
| bob | contractors | Ask; cannot see employee-only documents or request access |
| carol | managers, employees | Approve or deny access requests |
| dave | security-leads, employees | Decide requests after they escalate |

## Smoke test

```bash
curl -X POST localhost:8000/documents -H 'X-User-Id: alice' -H 'X-User-Groups: employees' \
  -F groups=employees,contractors -F 'file=@policy.txt;type=text/plain'
curl -X POST localhost:8000/agent/runs -H 'X-User-Id: alice' -H 'X-User-Groups: employees' \
  -H 'content-type: application/json' -d '{"question": "How do contractors get VPN access?"}'
curl localhost:8000/agent/runs/<run_id>
curl localhost:8000/approvals -H 'X-User-Id: carol' -H 'X-User-Groups: managers'
```

## Load test

```bash
python -m loadtest.burst --docs 200 --questions 50 --concurrency 50
```

## Local Kubernetes (the demo setup, $0)

Kubernetes runs inside Docker, so nothing extra is installed on your machine. Give Docker at least 6 CPUs and 8 GB first (Colima: `colima start --cpu 6 --memory 8`).

```bash
docker compose -f deploy/local/docker-compose.yml up -d
export KUBECONFIG=$PWD/deploy/local/.kube/kubeconfig.yaml
kubectl apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.17.2/keda-2.17.2.yaml
scripts/local-deploy.sh
kubectl -n antara exec deploy/ollama -- ollama pull qwen2.5:1.5b
kubectl -n antara exec deploy/ollama -- ollama pull nomic-embed-text
```

The app is at http://localhost:8000. `scripts/local-deploy.sh` rebuilds and rolls out after every code change. Demo documents are in `demo/policies/`; `python scripts/ask.py "question" --user alice --groups employees` runs one question from the terminal.

## Checks

Read [AGENTS.md](../AGENTS.md) first: services hold the rules, adapters hold the I/O, and every change is test-first.

| Command | What it proves | Needs |
| --- | --- | --- |
| `scripts/check.sh` | Lint, format, unit and workflow tests, 100% coverage of business logic | Docker |
| `scripts/integration.sh` | Every Postgres adapter against a real pgvector database | Docker |
| `python -m evals.run` | Model behaviour: right answers, right citations, no leaks, no invented confirmations | A running stack with models |

The eval suite must stay at 100%. When you find a new hallucination, add a case to `evals/cases.json` that reproduces it, watch it fail, then fix the harness.

## Frozen contracts

Everything in `common/` is shared by all four slices. Change it only after the team agrees.

| File | Holds |
| --- | --- |
| `common/names.py` | Workflow, activity, signal, query and error names |
| `common/queues.py` | Task queues: `ingest-cpu`, `ingest-gpu`, `agent`, `tools`, `reconciler` |
| `common/models.py` | Every payload passed through Temporal, plus `ChunkResult` and `UserContext` |
| `common/llm.py` | `LLMProvider`, the one client for chat and embeddings |
| `deploy/sql/` | App database schema, applied in file order |

## What each part does

| Folder | Owner | Holds |
| --- | --- | --- |
| `agent/` | Ranadhir | Agent loop, triage router, incident storm dedup, answer cache, approvals with escalation, tool registry, effectively-once tool ledger |
| `ingest/` | Prajval | Ingest workflow, CPU/GPU dispatcher, embedding fan-out, version publishing, permission-filtered search, deletion |
| `deploy/` | Manoj | Compose for local; k3s manifests, KEDA, GPU and model serving, NetworkPolicies and AWS scripts |
| `api/`, `gateway/`, `reconciler/` | Vinayak | API with backlog-based 429s, auth, web app; gateway limits, chaos flag and metrics; reconciler schedule |

AWS deployment steps are in [deploy/aws/README.md](../deploy/aws/README.md).
