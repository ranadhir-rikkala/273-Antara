import asyncio
import signal

from temporalio.testing import ActivityEnvironment

from common.worker_runtime import heartbeating, run_until_stopped


async def test_heartbeating_reports_progress_until_the_work_is_done() -> None:
    beats: list[tuple] = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: beats.append(details)

    async def work() -> str:
        async with heartbeating(every=0.01):
            await asyncio.sleep(0.05)
        count = len(beats)
        await asyncio.sleep(0.03)
        assert len(beats) == count
        return "done"

    assert await env.run(work) == "done"
    assert len(beats) >= 3


class FakeWorker:
    def __init__(self) -> None:
        self.stopped = asyncio.Event()
        self.shutdowns = 0

    async def run(self) -> None:
        await self.stopped.wait()

    async def shutdown(self) -> None:
        self.shutdowns += 1
        self.stopped.set()


async def test_a_stop_signal_shuts_the_worker_down_gracefully() -> None:
    worker = FakeWorker()
    running = asyncio.create_task(run_until_stopped(worker, signals=(signal.SIGUSR1,)))
    await asyncio.sleep(0.01)
    signal.raise_signal(signal.SIGUSR1)
    await asyncio.wait_for(running, timeout=1)
    assert worker.shutdowns == 1
