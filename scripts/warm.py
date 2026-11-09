import argparse
import asyncio
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

from agent.triage import classify_by_rules, needs_model
from common.models import Intent

PAGE = Path(__file__).resolve().parent.parent / "api" / "static" / "index.html"
DONE = {"COMPLETED", "DENIED", "FAILED", "WAITING_APPROVAL", "ESCALATED"}


class Samples(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.users: list[tuple[str, str]] = []
        self.chips: list[tuple[str, set[str]]] = []
        self.browse: list[str] = []
        self._select = ""
        self._option = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "select":
            self._select = values.get("id", "")
        elif tag == "option":
            self._option = True
            if self._select == "who" and "|" in values.get("value", ""):
                user, groups = values["value"].split("|", 1)
                self.users.append((user, groups))
        elif tag == "button" and "sample" in values.get("class", "").split():
            self.chips.append((values["data-q"], set(values["data-groups"].split(","))))

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = ""
        elif tag == "option":
            self._option = False

    def handle_data(self, data: str) -> None:
        if self._option and self._select == "helpdesk-samples" and data.strip() and not data.startswith("Choose"):
            self.browse.append(data.strip())


def plain_question(question: str) -> bool:
    intent = classify_by_rules(question)
    return intent == Intent.QUESTION and not needs_model(question, intent)


def plan(samples: Samples, browse: bool) -> list[tuple[str, str, str]]:
    jobs = []
    for user, groups in samples.users:
        mine = set(groups.split(","))
        questions = [question for question, allowed in samples.chips if allowed & mine]
        questions += samples.browse if browse else []
        jobs += [(user, groups, question) for question in dict.fromkeys(questions) if plain_question(question)]
    return jobs


async def ask(client: httpx.AsyncClient, user: str, groups: str, question: str, timeout: float) -> tuple[str, bool]:
    headers = {"X-User-Id": user, "X-User-Groups": groups}
    run_id = (await client.post("/agent/runs", json={"question": question}, headers=headers)).json()["run_id"]
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        response = await client.get(f"/agent/runs/{run_id}", headers=headers)
        if response.status_code == 200 and (state := response.json())["status"] in DONE:
            return state["status"], bool(state.get("cached"))
        await asyncio.sleep(0.5)
    return "TIMEOUT", False


async def warm(api: str, concurrency: int, timeout: float, browse: bool) -> None:
    samples = Samples()
    samples.feed(PAGE.read_text())
    jobs = plan(samples, browse)
    gate = asyncio.Semaphore(concurrency)
    tally: dict[str, list[int]] = {user: [0, 0, 0] for user, _ in samples.users}
    started = time.perf_counter()

    async def one(client: httpx.AsyncClient, user: str, groups: str, question: str) -> None:
        async with gate:
            status, cached = await ask(client, user, groups, question, timeout)
        tally[user][0 if cached else 1 if status == "COMPLETED" else 2] += 1
        print(f"{'cached' if cached else status.lower():<10} {user:<6} {question}")

    async with httpx.AsyncClient(base_url=api, timeout=30) as client:
        await asyncio.gather(*(one(client, *job) for job in jobs))
    print(f"\n{len(jobs)} questions in {time.perf_counter() - started:.0f}s")
    for user, (cached, fresh, other) in tally.items():
        print(f"  {user:<6} already cached {cached}, answered now {fresh}, other {other}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask every suggested question once per demo user to fill the cache")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--browse", action="store_true", help="also warm the Browse dropdown questions")
    args = parser.parse_args()
    asyncio.run(warm(args.api, args.concurrency, args.timeout, args.browse))


if __name__ == "__main__":
    main()
