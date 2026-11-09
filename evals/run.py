import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

CASES = Path(__file__).with_name("cases.json")
FIXTURES = Path(__file__).with_name("fixtures")
FIXTURE_GROUPS = "employees,contractors,managers"
RESULTS = Path(__file__).with_name("results")
SETTLED = {"COMPLETED", "DENIED", "FAILED", "WAITING_APPROVAL", "ESCALATED"}
CITATION_MARK = re.compile(r"\[(\d+)\]")


@dataclass
class Outcome:
    case_id: str
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures


def check(case: dict[str, Any], state: dict[str, Any], titles: dict[str, str]) -> list[str]:
    expect = case["expect"]
    if "http" in expect:
        return [] if state.get("http") == expect["http"] else [f"http {state.get('http', 202)} != {expect['http']}"]
    answer = (state.get("answer") or "").lower()
    cited = [titles[c["document_id"]] for c in state.get("citations", [])]
    tools = state.get("tool_results", [])
    failures = []
    if state["status"] != expect["status"]:
        failures.append(f"status {state['status']} != {expect['status']}")
    if "intent" in expect and state.get("intent") != expect["intent"]:
        failures.append(f"intent {state.get('intent')} != {expect['intent']}")
    if "top_citation" in expect and (not cited or cited[0] != expect["top_citation"]):
        failures.append(f"top citation {cited[:1]} != {expect['top_citation']}")
    for title in expect.get("never_cites", []):
        if title in cited:
            failures.append(f"leaked restricted document {title}")
    phrases = expect.get("answer_contains_any")
    if phrases and not any(phrase.lower() in answer for phrase in phrases):
        failures.append(f"answer has none of {phrases}")
    for phrase in expect.get("answer_excludes", []):
        if phrase.lower() in answer:
            failures.append(f"answer contains forbidden {phrase!r}")
    if "answer_starts_with" in expect and not answer.startswith(expect["answer_starts_with"].lower()):
        failures.append(f"answer does not start with {expect['answer_starts_with']!r}")
    if expect.get("no_tools") and tools:
        failures.append("a tool ran for a plain question")
    if "tools" in expect and len(tools) != len(expect["tools"]):
        failures.append(f"{len(tools)} tool runs, expected {len(expect['tools'])}")
    if expect.get("approval") and not state.get("approval_id"):
        failures.append("no approval was opened")
    if expect.get("incident") and not state.get("incident"):
        failures.append("no incident was recorded")
    for mark in CITATION_MARK.findall(answer):
        if not 1 <= int(mark) <= len(cited):
            failures.append(f"answer cites [{mark}] but only {len(cited)} passages exist")
    return failures


def run_case(client: httpx.Client, case: dict[str, Any], timeout: float) -> tuple[dict[str, Any], float]:
    headers = {"X-User-Id": case["user"], "X-User-Groups": case["groups"]}
    started = time.perf_counter()
    body = {"question": case["question"], "history": case.get("history", [])}
    response = client.post("/agent/runs", json=body, headers=headers)
    if response.status_code != 202:
        return {"http": response.status_code, "status": None}, time.perf_counter() - started
    run_id = response.json()["run_id"]
    while True:
        state = client.get(f"/agent/runs/{run_id}", headers=headers).json()
        if state["status"] in SETTLED or time.perf_counter() - started > timeout:
            return state, time.perf_counter() - started
        time.sleep(1.5)


def upload_fixtures(client: httpx.Client, timeout: float = 180) -> list[str]:
    headers = {"X-User-Id": "eval-fixtures", "X-User-Groups": FIXTURE_GROUPS}
    ids = []
    for path in sorted(FIXTURES.glob("*.md")):
        files = {"file": (path.name, path.read_bytes(), "text/plain")}
        response = client.post("/documents", files=files, data={"groups": FIXTURE_GROUPS}, headers=headers)
        ids.append(response.raise_for_status().json()["document_id"])
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        statuses = [client.get(f"/documents/{doc}", headers=headers).json()["versions"][0]["status"] for doc in ids]
        if all(status == "READY" for status in statuses):
            return ids
        time.sleep(2)
    raise RuntimeError(f"fixtures did not become READY: {statuses}")


def remove_fixtures(client: httpx.Client, ids: list[str]) -> None:
    for doc in ids:
        client.delete(f"/documents/{doc}", headers={"X-User-Id": "eval-fixtures", "X-User-Groups": FIXTURE_GROUPS})


def deny_leftover_approvals(client: httpx.Client) -> None:
    headers = {"X-User-Id": "eval-manager", "X-User-Groups": "managers"}
    for approval in client.get("/approvals", headers=headers).json():
        client.post(f"/approvals/{approval['approval_id']}/decision", json={"approved": False}, headers=headers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Antara model eval suite against a live API")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--cases", type=Path, default=CASES)
    args = parser.parse_args()

    cases = [case for case in json.loads(args.cases.read_text()) if not args.only or case["id"] in args.only]
    outcomes: list[Outcome] = []
    with httpx.Client(base_url=args.api, timeout=30) as client:
        fixture_ids = upload_fixtures(client)
        titles: dict[str, str] = {}
        for groups in ("employees,managers", "contractors"):
            for document in client.get("/documents", headers={"X-User-Id": "eval", "X-User-Groups": groups}).json():
                titles[document["document_id"]] = document["title"]
        for case in cases:
            state, seconds = run_case(client, case, args.timeout)
            for citation in state.get("citations", []):
                if citation["document_id"] not in titles:
                    reader = {"X-User-Id": case["user"], "X-User-Groups": case["groups"]}
                    titles[citation["document_id"]] = client.get(
                        f"/documents/{citation['document_id']}", headers=reader
                    ).json()["title"]
            outcome = Outcome(case["id"], check(case, state, titles), seconds)
            outcomes.append(outcome)
            mark = "PASS" if outcome.passed else "FAIL"
            print(f"{mark} {case['id']:<24} {seconds:6.1f}s {'; '.join(outcome.failures)}")
            if not outcome.passed:
                print(f"     answer: {(state.get('answer') or '')[:200]!r}")
        deny_leftover_approvals(client)
        remove_fixtures(client, fixture_ids)

    passed = sum(outcome.passed for outcome in outcomes)
    rate = passed / len(outcomes) if outcomes else 0.0
    print(f"\n{passed}/{len(outcomes)} passed ({rate:.0%})")
    RESULTS.mkdir(exist_ok=True)
    report = RESULTS / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    report.write_text(
        json.dumps(
            [{"id": o.case_id, "passed": o.passed, "failures": o.failures, "seconds": o.seconds} for o in outcomes],
            indent=2,
        )
    )
    sys.exit(0 if rate >= args.min_pass_rate else 1)


if __name__ == "__main__":
    main()
