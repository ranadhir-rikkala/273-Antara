import argparse
import asyncio
import random
import statistics
import time
from collections import Counter

import httpx

TOPICS = ["VPN", "password reset", "laptop encryption", "MFA", "email retention", "badge access", "Wi-Fi", "printing"]
QUESTIONS = [
    "How do contractors get VPN access?",
    "How often must I rotate my password?",
    "What is the policy on laptop disk encryption?",
    "Who approves production database access?",
    "How do I report a lost badge?",
]


def synthetic_policy(index: int) -> bytes:
    topic = random.choice(TOPICS)
    lines = [f"Policy {index}: {topic}"]
    for clause in range(1, 25):
        lines.append(f"{clause}. Employees must follow the {topic} standard, section {index}.{clause}, at all times.")
    return "\n".join(lines).encode()


async def upload(client: httpx.AsyncClient, index: int, codes: Counter, latencies: list[float]) -> None:
    started = time.perf_counter()
    response = await client.post(
        "/documents",
        files={"file": (f"policy-{index}.txt", synthetic_policy(index), "text/plain")},
        data={"groups": "employees,contractors"},
    )
    latencies.append(time.perf_counter() - started)
    codes[f"upload {response.status_code}"] += 1


async def ask(client: httpx.AsyncClient, codes: Counter, latencies: list[float]) -> None:
    started = time.perf_counter()
    response = await client.post("/agent/runs", json={"question": random.choice(QUESTIONS)})
    latencies.append(time.perf_counter() - started)
    codes[f"ask {response.status_code}"] += 1


async def bounded(semaphore: asyncio.Semaphore, job) -> None:
    async with semaphore:
        await job


async def main() -> None:
    parser = argparse.ArgumentParser(description="Burst load against the Antara API")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--docs", type=int, default=200)
    parser.add_argument("--questions", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--user", default="loadtest")
    parser.add_argument("--groups", default="employees")
    args = parser.parse_args()

    codes: Counter = Counter()
    latencies: list[float] = []
    semaphore = asyncio.Semaphore(args.concurrency)
    headers = {"X-User-Id": args.user, "X-User-Groups": args.groups}
    async with httpx.AsyncClient(base_url=args.api, headers=headers, timeout=60) as client:
        started = time.perf_counter()
        jobs = [bounded(semaphore, upload(client, i, codes, latencies)) for i in range(args.docs)]
        jobs += [bounded(semaphore, ask(client, codes, latencies)) for _ in range(args.questions)]
        random.shuffle(jobs)
        await asyncio.gather(*jobs)
        elapsed = time.perf_counter() - started

    print(f"Sent {args.docs} uploads and {args.questions} questions in {elapsed:.1f}s")
    for key, count in sorted(codes.items()):
        print(f"  {key}: {count}")
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[int(len(ordered) * 0.95) - 1]
        print(f"  latency median {statistics.median(ordered) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms")


if __name__ == "__main__":
    asyncio.run(main())
