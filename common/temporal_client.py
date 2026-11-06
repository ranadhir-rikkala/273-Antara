from temporalio.client import Client

from common.config import settings

_shared: Client | None = None


async def connect() -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def shared() -> Client:
    global _shared
    if _shared is None:
        _shared = await connect()
    return _shared
