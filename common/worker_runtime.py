import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator, Iterable
from typing import Protocol

from temporalio import activity


class StoppableWorker(Protocol):
    async def run(self) -> None: ...

    async def shutdown(self) -> None: ...


async def run_until_stopped(
    worker: StoppableWorker, signals: Iterable[signal.Signals] = (signal.SIGTERM, signal.SIGINT)
) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    running = asyncio.create_task(worker.run())
    await asyncio.wait([running, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
    if not running.done():
        await worker.shutdown()
    await running


@contextlib.asynccontextmanager
async def heartbeating(every: float = 10.0) -> AsyncIterator[None]:
    async def beat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(every)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
