import asyncio
import io

from minio import Minio
from urllib3.exceptions import MaxRetryError

from common.config import settings


def client() -> Minio:
    return Minio(
        settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        secure=False,
    )


def _ensure_bucket(minio: Minio) -> None:
    if not minio.bucket_exists(settings.s3_bucket):
        minio.make_bucket(settings.s3_bucket)


async def ensure_bucket(attempts: int = 30, delay_seconds: float = 1.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await asyncio.to_thread(_ensure_bucket, client())
            return
        except (OSError, MaxRetryError):
            if attempt == attempts:
                raise
            await asyncio.sleep(delay_seconds)


def _put(key: str, data: bytes, content_type: str) -> None:
    client().put_object(settings.s3_bucket, key, io.BytesIO(data), len(data), content_type=content_type)


def _get(key: str) -> bytes:
    response = client().get_object(settings.s3_bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _delete_prefix(prefix: str) -> int:
    minio = client()
    keys = [item.object_name for item in minio.list_objects(settings.s3_bucket, prefix=prefix, recursive=True)]
    for key in keys:
        minio.remove_object(settings.s3_bucket, key)
    return len(keys)


async def put_bytes(key: str, data: bytes, content_type: str) -> None:
    await asyncio.to_thread(_put, key, data, content_type)


async def get_bytes(key: str) -> bytes:
    return await asyncio.to_thread(_get, key)


async def delete_prefix(prefix: str) -> int:
    return await asyncio.to_thread(_delete_prefix, prefix)


class S3ObjectStore:
    async def get(self, key: str) -> bytes:
        return await get_bytes(key)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await put_bytes(key, data, content_type)

    async def delete_prefix(self, prefix: str) -> int:
        return await delete_prefix(prefix)
