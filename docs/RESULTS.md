# Measured results

Every number below was measured live on the local cluster in Colima on an Apple Silicon Mac: one k3s node (6 CPUs, 8 GB) until 2026-11-07, two nodes (8 CPUs, 16 GB) for the node and replication rows, CPU-only inference with `qwen2.5:1.5b` and `nomic-embed-text`. Single runs unless stated; treat them as observed values, not benchmarks.

## Failure recovery

| Drill | What happened | Measured |
| --- | --- | --- |
| Kill an agent worker mid-question | Replacement pod scheduled; the question finished on it | Replacement ready **3 s** after the kill; run completed |
| Model down for 30 s, question asked during it | Calls retried with backoff and succeeded after the outage | Answer in **~37 s** (twice) |
| GPU lane taken down, document uploaded | Embedding not picked up on the GPU queue within 20 s, then ran on CPU | Document READY in **22 s** |
| Index corrupted (a document's vectors deleted) | Reconciler detected and rebuilt it | Repaired in **28 s** |
| One ingest pod drained and one force-killed during an 80-document flood | Drained pod finished its work; the killed pod's work was detected by heartbeat and retried elsewhere | All **80/80** READY in **100 s**; **2** heartbeat timeouts, **0** lost |
| Same scenario before heartbeats and graceful shutdown | Work on a removed pod waited for the activity timeout | Documents stuck **5 min** |
| Tools worker force-killed 8 s after an access grant | The lease's durable timer was picked up by the replacement | Access revoked at **exactly** its expiry |
| Node 2 drained from the console while a question ran | Node cordoned; its 4 app pods deleted and recreated on node 1; restore rebalanced one copy per node | Node 2 empty in **~5 s**; question answered; balanced again in **~30 s** |
| Node 2 hard-stopped (`docker stop`), before the edge retry | Requests sent to the dead api copy timed out until Kubernetes marked the node NotReady | Requests failing for **~35 s**; node NotReady at **40 s** |
| Node 2 hard-stopped, with the edge retry (2 s connect timeout, 4 attempts) | Traefik retried on the surviving copy; replacements started on node 1 after the 20 s eviction delay | **1 / 65** requests failed, the rest in ≤ **2.2 s**; questions asked before and during the crash completed |
| Node 2 hard-stopped with every service at two copies | Traefik retried on node 1; the gateway failed over to the Ollama standby; Temporal moved the dead server's shards | **0 / 91** page requests failed (11 took 2 to 6 s); a question asked 2 s after the crash answered in **45 s**, later ones in **13 s** and **7 s** |
| Node 2 restarted after that crash | Pods rebalanced to one per node; both database standbys reconnected | Balanced **17 / 17** in **48 s**; standbys back to **0 bytes** of lag |
| Primary Ollama killed mid-question | Gateway connect failed within 2 s and the call went to the standby | Answer in **12.5 s**; one failover counted |
| One Temporal server killed mid-question | The surviving server took over its history shards | Two questions answered in **16 s** and **19 s** |
| Model outage on two gateway copies | The outage switch reached both copies | Question answered after the outage in **35 s** |
| Postgres write on a primary | Streamed to the standby on node 2 | Readable on the standby within **1 s**; lag **0 bytes**; the standby rejects writes |
| Document uploaded to the object store | `filer.sync` copied it to the replica | Both copies went from 45 to **47** files with identical sizes within seconds |
| One trace, two Jaegers | Two collectors sent every span to both | **41 / 41** spans on each copy |
| Four new questions at once, one vs two active Ollama copies on 8 shared CPUs | Two copies fought for the same cores | One copy: **20 to 40 s**, all answered. Two copies: **265 to 311 s**, one failed. Hence active/standby |
| **Whole node restarted** with a pending approval and a running lease | Pods came back; state in Postgres and Temporal intact | Serving again in **52 s**; 21/21 documents intact; the pending request was approved afterwards and completed; the lease revoked on schedule |

## Scaling, load balancing and backpressure

| Run | Result |
| --- | --- |
| Flood: 200 uploads + 50 questions | Ingest pods **1 → 4**, agent pods **1 → 3**; **33** uploads refused with 429 by the API (split **26 / 7** across the two replicas); **907** model calls refused with 429 by the gateway and retried; **0** failed; drained in **~6 min** |
| Flood: 200 uploads | **152** accepted, **48** refused with 429 (split **33 / 15** across replicas) |
| Flood: 100 uploads + 20 questions | **422** model-busy 429s, all retried; **0** failed |
| Two managers approve the same request at the same moment | One **202**, one **409**; exactly **one** grant |

## Data sovereignty and the AI harness

| Check | Result |
| --- | --- |
| Pods connect to `api.openai.com`, `api.anthropic.com`, `huggingface.co`, `1.1.1.1` | **4/4 refused** by the network policy (before the policy was applied locally, all were reachable) |
| Network calls in one answer's trace | **41 spans**; outbound calls only to `gateway:8080` and `ollama:11434` |
| Chat model swapped (pinned digest changed) | Every model call refused with **503 "Model not verified"**; restored on the real digest |
| Questions outside the documents (World Cup, CEO's address, stock price) | Refused without calling the model |
| Contractor asks for employee-only or security-leads-only facts | Never cited, never answered (12 cases) |
| Signing key requested by a security lead | Answer shows `[redacted]` |

## Answer latency

Measured by the eval runner, which polls every 1.5 s, across the 46 demo questions:

| Kind of answer | Time |
| --- | --- |
| Grounded answer from the model | **6 – 13 s** (most around 8 – 10 s) |
| Refusal (no permitted source) or cache hit | **under 2 s** (no model call) |
| Access request reaching "waiting for approval" | **~14 s** |

## Test suite

| Suite | Result |
| --- | --- |
| Unit and workflow tests (business logic at 100% line coverage, enforced on every commit) | **173 passed** |
| Integration tests on real Postgres | **20 passed** |
| Model evals | **28 / 28** |
| Demo question suite | **46 / 46** |
| Coverage of all code, unit + integration | **78%** (wiring and entry points are exercised by the live suites above) |

## Security review (attacks replayed after the fixes)

| Attack | Before | After |
| --- | --- | --- |
| Delete any document without logging in | 202 | **401** |
| Read another user's run (restricted passages) | 200 | **404** |
| Contractor publishes into security leads' documents | 202 | **403** |
| Upload 30 MB | 202 | **413** |
