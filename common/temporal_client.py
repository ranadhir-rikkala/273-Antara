from temporalio.client import Client

from common.config import settings


async def connect() -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
