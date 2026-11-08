# Design decisions

Short records of the choices that shape Antara, what we gave up, and when we would choose differently.

## 1. Temporal for messaging, not Kafka or RabbitMQ

**Decision.** Services talk through Temporal task queues and durable workflows.

**Why.** Antara's hard problems are long-running, stateful steps: wait up to 24 hours for a manager's approval, escalate after 2 minutes, revoke access when a lease ends, retry embedding for 30 minutes, resume a half-finished question on another pod. With Kafka or RabbitMQ we would still need to build durable timers, workflow state, retries with backoff, deduplication and a store for in-progress work. Temporal gives these as one tested mechanism, and its task queues still give us the messaging properties from module 5: asynchronous hand-off, competing consumers (pull-based load balancing), at-least-once delivery and a visible backlog that KEDA scales on.

**Trade-off.** Temporal is not an event log: there is no replay of a topic for new consumers, and throughput is lower than Kafka's. Antara has no high-volume event stream, so we do not need either.

**We would add Kafka** if many independent consumers needed the same event stream (for example analytics on every question), alongside Temporal rather than instead of it.

## 2. One Postgres with pgvector, not a separate vector database

**Decision.** Documents, permissions, chunks, vectors, approvals and audit live in one Postgres with pgvector.

**Why.** The permission filter and the vector search run in **one SQL query**, so a restricted chunk can never be a candidate (OWASP LLM08). Deleting a document hides it, removes its chunks and drops every cached answer that cited it in one transaction. One database to back up and operate.

**Trade-off.** A dedicated vector database scales further and offers approximate-search tuning. At thousands of chunks, Postgres is fast enough.

## 3. Small local models (Ollama, `qwen2.5:1.5b`), no fine-tuning

**Decision.** Chat and embeddings run inside the cluster; answers come only from retrieved documents (RAG).

**Why.** "Nothing leaves the network" rules out hosted models. A 1.5B model runs on a laptop CPU. Because it hallucinates more, the safety comes from the harness in code: permission-filtered retrieval, a relevance cutoff, injection stripping, secret redaction and approval-gated tools. Fine-tuning would bake documents into weights we cannot permission-filter or delete.

**Trade-off.** Answers take 6–13 s on CPU and are short. A larger model on a GPU node (the AWS path, vLLM) is a configuration change, not a code change: the model sits behind the gateway port.

## 4. Durable workflows instead of two-phase commit

**Decision.** Multi-step actions are sagas: each step commits locally, with idempotency keys and compensating actions (for example the lease's revoke), not distributed transactions.

**Why.** Two-phase commit across Postgres, the object store and Temporal would block on the slowest participant and needs a coordinator that can fail mid-protocol. Idempotency keys make retries safe (effectively-once), and the reconciler repairs anything left half-done.

## 5. Single node, single database: known single points of failure

**Decision.** The local cluster runs one k3s node, one app database, one Temporal database and one model server.

**What fails.** If `app-db` or `temporal-db` stops, new work waits; nothing is lost, because Temporal and Postgres persist state, and the full-node restart test recovered in 52 s with every pending approval and timer intact. Availability during the outage, however, is zero.

**Production path.** Managed Postgres with a synchronous standby for both databases, Temporal Cloud or a multi-node Temporal cluster, at least two model servers behind the gateway, and nodes across availability zones. This trades latency for availability exactly as CAP and PACELC predict: synchronous replication costs a round trip on every write.

## 6. Pull-based workers and per-queue scaling

**Decision.** Workers pull from their own queue and KEDA scales each worker type on its own backlog.

**Why.** A busy pod simply does not pull, so load spreads without a balancer, and one slow lane (embeddings) cannot starve another (questions). The GPU lane is a separate queue, so capability-aware scheduling is just which queue a task goes to, with a 20-second fallback to CPU.

## 7. Header sign-in for the demo, OIDC for real use

**Decision.** The demo trusts `X-User-Id` and `X-User-Groups`; the same code supports OIDC tokens.

**Why.** It lets one laptop show five people with different access. **It is not authentication**: anyone who reaches the app can claim any identity. Real deployments run with `AUTH_MODE=oidc`, where identity comes from a signed token.

## 8. GPU lane simulated locally

The laptop has no NVIDIA GPU, so the `ingest-gpu` workers run on CPU locally. The scheduling mechanism (separate queue, pickup timeout, fallback) is real; the hardware speed-up is not. On AWS the same deployment runs on a GPU node.
