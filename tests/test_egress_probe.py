import asyncio

from api.ops.adapters import SocketEgressProbe


async def test_probe_reports_a_listening_port_as_reachable_and_a_closed_one_as_blocked() -> None:
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        assert await SocketEgressProbe(timeout=1).reach("127.0.0.1", port) == (True, "connected")
    assert await SocketEgressProbe(timeout=1).reach("127.0.0.1", port) == (False, "connection refused")


def test_probe_explains_every_way_a_connection_fails() -> None:
    explain = SocketEgressProbe.explain
    assert explain(
        OSError("Multiple exceptions: [Errno 111] Connect call failed, [Errno 101] Network is unreachable")
    ) == ("connection refused")
    assert explain(OSError(101, "Network is unreachable")) == "network unreachable"
    assert explain(OSError(-2, "Name or service not known")) == "Name or service not known"
    assert explain(OSError()) == "OSError"


async def test_probe_gives_up_after_its_timeout() -> None:
    reachable, detail = await SocketEgressProbe(timeout=0.2).reach("10.255.255.1", 443)
    assert (reachable, detail) == (False, "no answer within 0.2 s")
