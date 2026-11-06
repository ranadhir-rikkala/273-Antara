from temporalio.client import Client

from common.config import settings
from common.telemetry import setup_tracing, temporal_interceptors, temporal_runtime

_shared: Client | None = None


async def connect() -> Client:
    setup_tracing()
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=temporal_interceptors(),
        runtime=temporal_runtime(),
    )


async def shared() -> Client:
    global _shared
    if _shared is None:
        _shared = await connect()
    return _shared
