# Demo script (about 14 minutes)

Two browser windows side by side: the **helpdesk** (`http://localhost:8000`) on the left, the **operations console** (`http://localhost:8000/ops`) on the right. Every number quoted below was measured; see [RESULTS.md](RESULTS.md).

## Pre-flight (30 minutes before)

1. Mac plugged in, sleep off: `caffeinate -d` in a terminal.
2. Cluster up: `colima start --cpu 6 --memory 8 && docker start antara-k3s-k3s-1`, then `kubectl -n antara get pods` until all 17 are `1/1 Running`.
3. Warm up and verify: `python -m evals.run --cases evals/demo.json` must print **46/46**. This also warms the model.
4. Close leftover test incidents so the console starts clean:
   `kubectl -n antara exec app-db-0 -- psql -U antara -c "update incidents set status='CLOSED', updated_at=now() where status='OPEN'"`
5. Open both windows and hard-refresh (Cmd+Shift+R). On the console, click **Run egress check** once so "Egress: Blocked" shows.
6. Open Grafana (`/grafana`) and Jaeger (`/jaeger`) in tabs.
7. Keep a screen recording of a full run as a backup.

## Run of show

| Time | Act | Do | Say / point at |
| --- | --- | --- | --- |
| 0:00 | **Problem** | — | "Companies want an AI helpdesk, but they can't send internal documents to OpenAI. Antara answers from company documents, respects who may see what, and nothing leaves the network. It's a distributed system: ten mechanisms from the course, live." |
| 1:00 | **Start a lease early** | Helpdesk as **alice**: **More** → *I need read access to the production database…* Switch to **carol**, Approve in the inbox. | "Access needs a manager. The request waited durably in a Temporal workflow. Note the answer: it ends automatically in 3 minutes. We'll come back to it." (M5, M9) |
| 2:30 | **Grounded answers** | Back to **alice**: click **Prod cluster access**. Open the sources. | The harness receipt: network, access, grounding, filters, actions. "Every check is code, not the prompt." |
| 3:15 | **Access control** | Switch to **bob (contractor)**: click **API rate limits**. | Locked chip; answer "couldn't find"; receipt: "Refused: no permitted source". "Permissions are applied inside the vector search, before ranking." (OWASP LLM08) |
| 3:45 | **Secrets** | Switch to **dave**: click **Signing key**. | `[redacted]` and "Secret redacted" on the receipt. |
| 4:15 | **Nothing leaves** | Console: **Run egress check**. Helpdesk: **View trace** on any answer. | 4/4 outside AI services blocked; the trace shows API → Temporal → agent → gateway → model, all internal. |
| 5:15 | **Failure recovery** | Helpdesk (alice): ask *How long must my password be?* Immediately, console: **Terminate agent pod** → Run. | The answer still arrives. The Failure recovery row shows "agent recovered in N s"; the Event log shows the replacement. (M1, M7) |
| 6:30 | **Retries** | Console: **Model outage · 30 s** → Run. Helpdesk: ask *What disk encryption do we use on Macs?* | Health shows Model: Down, then Healthy. The answer arrives after the outage (~37 s). (M3, M6) |
| 7:45 | **GPU lane** | Console: **Disable GPU lane** → Run. Helpdesk: **Upload document** (any small `.md`). Then **Restore GPU lane**. | The Event log shows the experiment; the document is READY in ~20 s via CPU. "Capability-aware scheduling: GPU first, CPU after 20 seconds." (M6) |
| 9:00 | **Reconciliation** | Console: **Corrupt vector index** → Run. | Within ~30 s the Event log shows the repair; the State reconciliation row stays at 0 stuck. (M4, M8) |
| 10:00 | **Lease revoked** | Console: Event log. | "The 3-minute lease from the start has ended: a durable timer revoked it. That timer survives even a full node restart; we measured it." (M7, M9) |
| 10:30 | **Scale and backpressure** | Console: **Load test** → Run. Switch to the Grafana tab after 30 s. | Services table: ingest 1→4, agent 1→3 (KEDA). The API 429s and Gateway 429s figures climb: overload gets 429, not a crash. (M2, M3, M4, M6) |
| 12:30 | **Close** | Show [SECURITY.md](SECURITY.md) or the slide. | OWASP LLM Top 10: 10/10. Measured results. Limits: single node and single databases locally; the production path is replicas. |

**Optional finale (30 s):** turn off the Mac's Wi-Fi and ask one more question. It still answers.

## If something goes wrong

| Symptom | Do |
| --- | --- |
| An answer takes more than 20 s | Say "the model is on a laptop CPU" and click a sample you already asked; it comes from the cache in under 2 s. |
| An experiment errors | Skip it and point at [RESULTS.md](RESULTS.md) for the measured value. |
| The console says "Connecting" | `kubectl -n antara get pods`; if pods restarted, wait 60 s. |
| Everything is down (Mac slept) | Rerun the pre-flight step 2 (the system recovered in 52 s in testing), or switch to the recording. |
| The flood slows everything | Expected. Do the flood last; it drains in about 6 minutes. |

## Questions to expect

| Question | Short answer |
| --- | --- |
| Why Temporal and not Kafka or RabbitMQ? | Our hard parts are durable timers, waiting for approvals, retries and resuming half-finished work. Temporal gives those plus task queues with competing consumers and backlog-based scaling. Kafka would add an event log we do not need. See [DECISIONS.md](DECISIONS.md) §1. |
| What happens if Postgres dies? | New work waits; nothing is lost, and after a full node restart everything resumed in 52 s. But availability is zero during the outage: it is a single point of failure locally. Production uses a synchronous standby: availability bought with write latency, as CAP and PACELC predict. |
| Is it exactly-once? | No: effectively-once. Activities are at-least-once; idempotency keys make repeated tool calls return the stored result, and two simultaneous approvals produced exactly one grant. |
| How do you stop the model making things up? | If no permitted passage scores 0.6 or more, the model is never called. Every answer cites sources, and the eval suite checks 74 questions. |
| Can a contractor see salaries? | No. The vector search filters by group inside the query; 12 eval cases check every sensitive fact as an unauthorised user. |
| How is load balanced? | Workers pull from their queue, so a busy pod simply doesn't take the next task; two API replicas sit behind a Kubernetes Service. The flood split refusals 26/7 and 33/15 across the replicas. |
| Is the GPU real? | Not on the laptop: the GPU lane runs on CPU locally. The scheduling and fallback are real; on AWS it runs on a GPU node. |
| Is the login real? | The demo trusts the user named in the request so one laptop can show five people. Real deployments use OIDC tokens. |
| How do you know nothing leaves? | A default-deny network policy, an on-demand check that tries OpenAI, Anthropic and Hugging Face from inside the cluster, a trace of every answer's calls, and pinned model and image digests. |
