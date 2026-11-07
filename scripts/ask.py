import argparse
import json
import time

import httpx

WAITING = {"COMPLETED", "DENIED", "FAILED", "WAITING_APPROVAL", "ESCALATED"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the Antara helpdesk a question and print the result")
    parser.add_argument("question")
    parser.add_argument("--user", default="alice")
    parser.add_argument("--groups", default="employees")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()

    headers = {"X-User-Id": args.user, "X-User-Groups": args.groups}
    with httpx.Client(base_url=args.api, headers=headers, timeout=30) as client:
        started = time.perf_counter()
        run_id = client.post("/agent/runs", json={"question": args.question}).raise_for_status().json()["run_id"]
        while True:
            state = client.get(f"/agent/runs/{run_id}").json()
            if state["status"] in WAITING or time.perf_counter() - started > args.timeout:
                break
            time.sleep(1.5)
    elapsed = time.perf_counter() - started

    print(f"[{state['status']}] intent={state['intent']} cached={state['cached']} {elapsed:.1f}s run={run_id}")
    print(f"answer: {state['answer']}")
    for index, citation in enumerate(state["citations"], start=1):
        print(f"  [{index}] score={citation['score']:.2f} {citation['text'][:70]!r}")
    if state.get("incident"):
        print(f"incident: {json.dumps(state['incident'])}")
    if state.get("approval_id"):
        print(f"approval_id: {state['approval_id']} escalated={state['escalated']}")
    for result in state["tool_results"]:
        print(f"tool: {result['status']} {json.dumps(result['output'])}")


if __name__ == "__main__":
    main()
