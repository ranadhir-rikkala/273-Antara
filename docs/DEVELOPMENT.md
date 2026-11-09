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

Kubernetes runs inside Docker, so nothing extra is installed on your machine. Give Docker at least 8 CPUs and 12 GB first (Colima: `colima start --cpu 8 --memory 12`).

The cluster has two k3s nodes. Node 1 is the control plane and holds everything with a disk (Postgres, Temporal, the object store, Ollama) plus monitoring. Node 2 is tainted for the app tier only: api, agent, tools and ingest-cpu run one copy on each node, so losing either node leaves a working copy of the request path. `scripts/local-cluster.sh` starts both nodes (and raises the VM's inotify limit, which two nodes need).

```bash
scripts/local-cluster.sh
export KUBECONFIG=$PWD/deploy/local/.kube/kubeconfig.yaml
kubectl apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.17.2/keda-2.17.2.yaml
scripts/local-deploy.sh
kubectl -n antara delete networkpolicy default-deny
kubectl -n antara exec deploy/ollama -- ollama pull qwen2.5:1.5b
kubectl -n antara exec deploy/ollama -- ollama pull nomic-embed-text
scripts/local-deploy.sh
```

Grafana, Jaeger, Prometheus and the Temporal UI are at `/grafana`, `/jaeger`, `/prometheus` and `/temporal`, with no login: anyone who can reach the app can read them, including every question and answer in the Temporal UI. The Temporal UI is read-only.

Pods cannot reach the internet: a default-deny NetworkPolicy allows traffic only between pods in the namespace, to cluster DNS, and to the Kubernetes API on private addresses. Downloading the models is the one step that needs the internet, so it lifts the policy for the download and `scripts/local-deploy.sh` puts it back. Models stay on the Ollama volume afterwards.

To crash node 2 for real, `scripts/local-cluster.sh stop-worker`; `scripts/local-cluster.sh start-worker` brings it back and rebalances the request path.

The app is at http://localhost:8000. `scripts/local-deploy.sh` rebuilds and rolls out after every code change. Demo documents are in `demo/policies/`; `python scripts/ask.py "question" --user alice --groups employees` runs one question from the terminal.

## Checks

Read [AGENTS.md](../AGENTS.md) first: services hold the rules, adapters hold the I/O, and every change is test-first.

| Command | What it proves | Needs |
| --- | --- | --- |
| `scripts/check.sh` | Lint, format, unit and workflow tests, 100% coverage of business logic | Docker |
| `scripts/integration.sh` | Every Postgres adapter against a real pgvector database | Docker |
| `python -m evals.run` | Model behaviour: right answers, right citations, no leaks, no invented confirmations | A running stack with models |
| `python -m evals.run --cases evals/demo.json` | The 46 questions most likely in the live demo, across every user and harness layer. Run it before every demo | A running stack with the demo documents |

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

Each person owns one part: its code, its tests, its promises and its live demo moment. Commit your own work, in your own folders, under your own git name. Changes to someone else's part need a review from its owner.

| Folder | Owner | Holds |
| --- | --- | --- |
| `agent/`, `evals/` | Ranadhir | Agent loop, triage router, incident storm dedup, answer cache, approvals with escalation, tool registry, effectively-once tool ledger, model evals |
| `ingest/`, `demo/` | Prajval | Ingest workflow, CPU/GPU dispatcher, embedding fan-out, version publishing, permission-filtered search, deletion, demo documents |
| `api/` (except `api/ops/`), `gateway/`, `reconciler/` | Vinayak | API with backlog-based 429s, auth, helpdesk web app; gateway limits and metrics; reconciler schedule and repairs |
| `deploy/`, `api/ops/`, `loadtest/`, `scripts/` | Manoj | Compose and k3s manifests, KEDA, GPU and model serving, NetworkPolicies, observability, AWS scripts; ops console, chaos actions and flood |

Shared by everyone, reviewed by Ranadhir: `common/`, `deploy/sql/`, `tests/fakes.py`, `AGENTS.md`.

| Owner | Promises they present | Live demo moment |
| --- | --- | --- |
| Ranadhir | 1 async messaging, 5 reliable messaging, 7 failure recovery, effectively-once tools, incident fan-out/fan-in | Kill the agent worker mid-question; the answer still arrives |
| Prajval | 6 retries, 10 GPU lane with CPU fallback, data lifecycle | Lose the GPU lane; a new document is still READY via CPU |
| Vinayak | 3 load balancing, 4 backpressure, 8 state reconciliation | Flood: 429s from both API replicas and the model gateway; corrupt the index and watch it repair |
| Manoj | 2 independent scaling (KEDA), 9 distributed monitoring | Worker pods scale out under the flood on the ops console, Grafana and Jaeger |

### Next tasks by owner

| Owner | Task | Course module |
| --- | --- | --- |
| Ranadhir | Incident command: status updates to reporters and auto-close; send the updates through a transactional outbox so none is lost or sent twice | 4, 9 |
| Prajval | Seed script that uploads `demo/policies/` with the right groups for each document | 2 |
| Prajval | Fencing on document versions: activate a version only if it is newer than the active one, so a slow, stale ingest can never replace a newer document | 7, 9 |
| Vinayak | Separate the rules in `api/runs.py` and `api/auth.py` from Temporal and FastAPI, then add both to `tests/test_architecture.py` | 4 |
| Vinayak | Circuit breaker in front of the model: stop calling it after repeated failures, then let one trial call through before reopening | 4, 6 |
| Vinayak | Demo UI: helpdesk and ops console side by side, with guided steps | — |
| Manoj | Automated chaos suite that runs every chaos action and prints the measured numbers | 6 |
| Manoj | CI on GitHub Actions: `scripts/check.sh` on every push | 2 |
| Manoj | Rehearse the demo twice with `docs/DEMO.md`, time each act, fix the script where reality differs, and record a backup video | — |
| Manoj | Resource requests and limits on every app pod, and run the image as a non-root user | 2, 6 |
| Manoj | Prometheus alert rules: pods not ready, queue backlog growing, 429 rate, model down | 6 |
| Manoj | Backup and restore drill for the app database, measured against the 15-minute RPO | 8 |
| Vinayak | Per-user rate limit, so one user cannot use up the shared admission budget | 4 |

Already built from the course, for the report: graceful worker shutdown with activity heartbeats (module 6), and self-revoking access as a lease whose durable timer runs the compensating revoke (modules 7 and 9).

Everyone shares the report and slides; each person writes the section for the promises they present. If you use an AI coding assistant, point it at [AGENTS.md](../AGENTS.md): business rules stay in `services.py` behind `ports.py`, and every outside system is reached through an adapter, so any component can be swapped later. `tests/test_architecture.py` enforces this: it fails if a service or port imports anything but the standard library, `common/models.py`, `common/names.py`, `common/queues.py` or other business logic. Add each new service or port file to its list.

AWS deployment steps are in [deploy/aws/README.md](../deploy/aws/README.md).
