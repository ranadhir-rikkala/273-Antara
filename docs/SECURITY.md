# Security: OWASP Top 10 for LLM Applications (2025)

Antara is checked against the [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/), the industry list of the most serious risks in applications built on language models. Every control below lives in code, not in the prompt, and every row names the test or eval that proves it.

| # | Risk | How Antara handles it | Proof |
| --- | --- | --- | --- |
| LLM01 | Prompt injection | Retrieved passages are untrusted data: sentences carrying instructions are stripped before the model sees them (`agent/guard.py`), and the system prompt says passages must never be obeyed. Tools are only offered for action requests, so an injected instruction cannot call one. | `tests/test_guard.py`; evals `poisoned-document`, `prompt-injection-direct` |
| LLM02 | Sensitive information disclosure | Search returns only documents the asker's groups may read. Secrets and canary tokens are redacted from every answer (`agent/guard.py`). A run, a document and its details are visible only to people allowed to see them (`api/services.py`). | Evals `canary-never-leaks`, `signing-key-redacted`, every `*-contractor` and `*-leak` case; `tests/test_api_routes.py` |
| LLM03 | Supply chain | Every third-party container image is pinned by `sha256` digest in `deploy/`. The model gateway checks each model's digest against the pinned value before serving, re-checks every minute, and refuses every model call with 503 if a model is missing or different (`gateway/services.py`). | `tests/test_gateway_services.py`; live: a swapped chat model was refused with `Model not verified: qwen2.5:1.5b` |
| LLM04 | Data and model poisoning | No fine-tuning, so there is no training data to poison. Users can share documents only with their own groups (security leads and operators publish policy), so a contractor cannot plant text in what security leads read. Uploads over 5 MB are refused. | `tests/test_api_services.py`, `tests/test_api_routes.py` |
| LLM05 | Improper output handling | The web pages insert answers as plain text, never as HTML, so an answer cannot run script. Tool arguments are validated and normalised before use, and the confirmation a user sees is built from the tool's real result, not from the model's words (`agent/tools.py`, `agent/summary.py`). | `tests/test_tools.py`, `tests/test_summary.py` |
| LLM06 | Excessive agency | Tools are offered only for action requests and only the one that matches. Each tool checks the asker's group, access needs a manager's approval (escalated to security leads), every action runs once per idempotency key, and access expires on a lease. | Evals `contractor-access-denied`, `access-request-waits`; `tests/test_agent_workflow.py` |
| LLM07 | System prompt leakage | The system prompt holds no secrets, credentials or access rules; all of those are enforced in code. Asking for the prompt is tested. | Demo eval `prompt-injection-direct` |
| LLM08 | Vector and embedding weaknesses | Permissions are applied inside the vector search query, before ranking (`ingest/postgres.py`), so a restricted chunk is never a candidate. The answer cache is keyed by the asker's groups and dropped when a cited document is deleted. | `tests/integration/test_ingest_adapters.py::test_search_respects_document_permissions_and_ranks_by_similarity`; `tests/integration/test_agent_adapters.py` |
| LLM09 | Misinformation | If no passage scores at least 0.6, the model is never called and the answer says it could not find it. Every answer cites its sources, and citation numbers are checked. | Evals `unknown-topic`, `unknown-topic-near-miss`; demo evals `world-cup`, `ceo-address`, `stock-price` |
| LLM10 | Unbounded consumption | Requests are refused with 429 and `Retry-After` when the API's in-flight limit, queue backlogs or documents in flight pass their limits, and the gateway caps concurrent model calls. Questions are capped at 2,000 characters and uploads at 5 MB. | `tests/test_admission.py`, `tests/test_gateway_services.py`; the flood drill (48 uploads refused live) |

## Beyond the Top 10: nothing leaves the network

A default-deny NetworkPolicy lets pods talk only to each other, to cluster DNS and to the Kubernetes API on private addresses. The operations console can prove it on demand by trying to reach OpenAI, Anthropic and Hugging Face from inside the cluster, and every answer's trace lists the hosts it called.

## Known limits

- Header sign-in (the demo mode) trusts the user named in the request. Real deployments use the OIDC mode, where identity comes from a signed token.
- There is no per-user rate limit yet; the limits above are shared by everyone.
- The application image `antara-app` is built from this repository on each deploy rather than pinned by digest.
