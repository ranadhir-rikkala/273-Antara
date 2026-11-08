from typing import Protocol


class Upstream(Protocol):
    async def post(self, path: str, body: bytes) -> tuple[int, bytes]: ...


class GatewayMetrics(Protocol):
    def rejected(self, kind: str, reason: str) -> None: ...

    def started(self, kind: str) -> None: ...

    def finished(self, kind: str) -> None: ...


class ModelCatalog(Protocol):
    async def digests(self) -> dict[str, str]: ...
