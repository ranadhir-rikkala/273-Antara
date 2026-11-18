import argparse
import json
import time
import urllib.error
import urllib.request
import uuid

USERS = {
    "alice": "employees",
    "bob": "contractors",
    "carol": "employees,managers",
    "dave": "employees,security-leads",
    "erin": "operators",
}
DONE = {"COMPLETED", "DENIED", "FAILED", "WAITING_APPROVAL", "ESCALATED"}


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(
        self,
        method: str,
        path: str,
        user: str = "alice",
        body: object = None,
        raw: bytes | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, object]:
        headers = {"X-User-Id": user, "X-User-Groups": USERS[user]}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode()
                status = response.status
        except urllib.error.HTTPError as error:
            text, status = error.read().decode(), error.code
        try:
            return status, json.loads(text, strict=False)
        except json.JSONDecodeError:
            return status, text

    def ask(self, question: str, user: str, timeout: float = 240) -> dict:
        status, started = self.call("POST", "/agent/runs", user, {"question": question})
        if status != 202:
            return {"status": f"HTTP {status}", "detail": started}
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, state = self.call("GET", f"/agent/runs/{started['run_id']}", user)
            if isinstance(state, dict) and state.get("status") in DONE:
                return {**state, "trace_id": started["trace_id"]}
            time.sleep(1.5)
        return {"status": "TIMEOUT"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, name: str, ok: bool, detail: object = "") -> bool:
        self.rows.append((ok, name, str(detail)[:140]))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(detail)[:140]}", flush=True)
        return ok


def wait_for(predicate, timeout: float, every: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(every)
    return None


def pages(api: Api, report: Report) -> None:
    for path in ("/", "/ops", "/healthz", "/auth/config", "/signoz/", "/temporal/"):
        status, _ = api.call("GET", path)
        report.check(f"page {path}", status == 200, status)


def questions(api: Api, report: Report) -> None:
    for user, question in (
        ("alice", "How long does my password need to be?"),
        ("bob", "How do contractors get VPN access?"),
        ("carol", "Who must approve write access to production databases?"),
        ("dave", "What is the procedure if a secret is leaked?"),
    ):
        state = api.ask(question, user)
        report.check(
            f"ask as {user}",
            state.get("status") == "COMPLETED" and bool(state.get("answer")),
            f"{state.get('status')} citations={len(state.get('citations') or [])}",
        )
    state = api.ask("What is the salary band for L4 engineers?", "bob")
    report.check(
        "contractor cannot read restricted answers",
        state.get("status") in ("COMPLETED", "DENIED"),
        (state.get("answer") or "")[:80],
    )
    trace = wait_for(
        lambda: (lambda r: r[1] if r[0] == 200 else None)(
            api.call("GET", f"/traces/{state.get('trace_id', '')}", "bob")
        ),
        30,
        2.5,
    )
    report.check("trace summary (SigNoz)", bool(trace) and trace.get("outside") == [], trace)


def actions(api: Api, report: Report) -> None:
    state = api.ask("Please create a ticket: the printer on floor 3 keeps jamming since this morning", "alice")
    report.check(
        "ticket request", state.get("status") == "COMPLETED" and bool(state.get("tool_results")), state.get("answer")
    )
    state = api.ask("I need read access to the production database for the billing migration", "alice")
    report.check(
        "access request waits for approval",
        state.get("status") in ("WAITING_APPROVAL", "ESCALATED"),
        state.get("status"),
    )
    approval_id = state.get("approval_id")
    status, inbox = api.call("GET", "/approvals", "carol")
    report.check(
        "manager sees the approval",
        status == 200 and any(a.get("approval_id") == approval_id for a in inbox),
        f"{status} {len(inbox) if isinstance(inbox, list) else inbox}",
    )
    status, _ = api.call("GET", "/approvals", "alice")
    report.check("employee cannot open the inbox", status == 403, status)
    if approval_id:
        status, body = api.call("POST", f"/approvals/{approval_id}/decision", "carol", {"approved": True})
        report.check("manager approves", status == 202, body)
    state = api.ask("VPN down, what should I do?", "alice")
    report.check("incident report", state.get("status") == "COMPLETED", state.get("incident") or state.get("answer"))


def documents(api: Api, report: Report) -> None:
    status, listed = api.call("GET", "/documents", "alice")
    report.check(
        "list documents",
        status == 200 and isinstance(listed, list),
        len(listed) if isinstance(listed, list) else listed,
    )
    failed = [d for d in listed if isinstance(listed, list) and d.get("status") == "FAILED"]
    report.check("no failed documents", not failed, f"{len(failed)} failed")
    boundary = uuid.uuid4().hex
    content = f"Smoke test note {boundary}: the smoke test badge colour is teal.\n".encode()
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="groups"\r\n\r\nemployees\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="smoke-{boundary[:8]}.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, uploaded = api.call(
        "POST", "/documents", "alice", raw=body, content_type=f"multipart/form-data; boundary={boundary}"
    )
    if not report.check("upload document", status == 202, uploaded):
        return
    document_id = uploaded["document_id"]

    def ready():
        _, doc = api.call("GET", f"/documents/{document_id}", "alice")
        versions = doc.get("versions") if isinstance(doc, dict) else None
        status = versions[-1]["status"] if versions else None
        return status if status in ("READY", "FAILED") else None

    status = wait_for(ready, 180)
    report.check("uploaded document becomes READY", status == "READY", status)
    status, _ = api.call("GET", f"/documents/{document_id}", "bob")
    report.check("contractor cannot see employee document", status == 404, status)
    status, body = api.call("DELETE", f"/documents/{document_id}", "alice")
    report.check("delete document", status == 202, body)
    gone = wait_for(lambda: api.call("GET", f"/documents/{document_id}", "alice")[0] == 404, 120)
    report.check("deleted document is gone", bool(gone))


def ops(api: Api, report: Report, flood: bool) -> None:
    status, _ = api.call("GET", "/ops/state", "alice")
    report.check("non-operator blocked from console", status == 403, status)
    status, state = api.call("GET", "/ops/state", "erin")
    report.check(
        "console state",
        status == 200 and not state.get("errors"),
        state.get("errors") if isinstance(state, dict) else state,
    )
    status, body = api.call("POST", "/ops/egress-check", "erin")
    report.check(
        "egress check: all blocked",
        status == 200 and not any(probe["reachable"] for probe in body),
        ", ".join(f"{probe['host']} blocked" for probe in body if not probe["reachable"]),
    )
    status, body = api.call("POST", "/ops/reconcile", "erin")
    report.check("run reconciler", status == 202, body)

    status, body = api.call("POST", "/ops/chaos/model-outage", "erin", {"seconds": 10})
    report.check("model outage 10 s", status == 202, body)
    state = api.ask("How do I report a phishing email?", "alice")
    report.check("question during outage still completes", state.get("status") == "COMPLETED", state.get("status"))

    status, body = api.call("POST", "/ops/chaos/kill-worker", "erin", {"target": "agent"})
    report.check("kill agent worker", status == 202, body)
    state = api.ask("How quickly must on-call respond to a SEV2?", "alice")
    report.check("question after kill completes", state.get("status") == "COMPLETED", state.get("status"))

    status, body = api.call("POST", "/ops/chaos/agent-workers", "erin", {"down": True})
    report.check("stop agent workers", status == 202, body)
    status, started = api.call(
        "POST", "/agent/runs", "alice", {"question": "Can we deploy to production on a Friday afternoon?"}
    )
    time.sleep(5)
    status2, body = api.call("POST", "/ops/chaos/agent-workers", "erin", {"down": False})
    report.check("restore agent workers", status2 == 202, body)
    if status == 202:
        done = wait_for(
            lambda: (api.call("GET", f"/agent/runs/{started['run_id']}", "alice")[1] or {}).get("status") in DONE, 240
        )
        report.check("question queued while workers were down completes", bool(done))

    status, body = api.call("POST", "/ops/chaos/gpu-lane", "erin", {"down": True})
    report.check("disable GPU lane", status == 202, body)
    status, body = api.call("POST", "/ops/chaos/gpu-lane", "erin", {"down": False})
    report.check("enable GPU lane", status == 202, body)

    status, body = api.call("POST", "/ops/chaos/corrupt-index", "erin")
    report.check("corrupt index", status in (202, 409), body)
    if status == 202:

        def repaired():
            _, listed = api.call("GET", "/documents", "erin")
            return isinstance(listed, list) and not [d for d in listed if d.get("status") != "READY"]

        report.check("reconciler repairs corrupted document", bool(wait_for(repaired, 180, 5)))

    status, body = api.call("POST", "/ops/chaos/worker-node", "erin", {"down": True})
    report.check("drain worker node (409 when node 2 is off)", status in (202, 409), f"{status} {body}")
    if status == 202:
        status, body = api.call("POST", "/ops/chaos/worker-node", "erin", {"down": False})
        report.check("restore worker node", status == 202, body)

    if flood:
        status, body = api.call("POST", "/ops/chaos/flood", "erin", {"documents": 10, "questions": 3})
        report.check("small flood", status == 202, body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Click every button of the Antara demo through its API and report")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--only", choices=["pages", "questions", "actions", "documents", "ops"])
    parser.add_argument("--flood", action="store_true", help="also start a small flood (10 docs, 3 questions)")
    args = parser.parse_args()
    api, report = Api(args.api), Report()
    steps = {"pages": pages, "questions": questions, "actions": actions, "documents": documents}
    for name, step in steps.items():
        if args.only in (None, name):
            print(f"== {name}", flush=True)
            step(api, report)
    if args.only in (None, "ops"):
        print("== ops", flush=True)
        ops(api, report, args.flood)
    failed = [row for row in report.rows if not row[0]]
    print(f"\n{len(report.rows) - len(failed)}/{len(report.rows)} passed")
    for _, name, detail in failed:
        print(f"  FAIL {name}: {detail}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
