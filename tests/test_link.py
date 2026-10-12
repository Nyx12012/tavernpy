import asyncio
import json

import pytest

from tavernpy import (CommandTimeoutException, ConsoleClosedException, ConsoleCreateFailedException,
                      ConsoleNotConnectedException, ConsolePermissionsDenied, Error, Reply, read_token)
from tavernpy.link import Link

from .conftest import wait_for
from .fake import free_port


def link_for(port, token="good.token.sig", wanted=(), seen=None, **kw):
    kw.setdefault("settle_delays", ())
    names = list(wanted)

    def emit(name, data, timestamp, source):
        if seen is not None:
            seen.append({"name": name, "data": data, "timestamp": timestamp, "source": source})

    return Link("127.0.0.1", port, token, wanted=lambda: names, emit=emit, **kw), names


async def test_connect_and_round_trip(fake):
    link, _ = link_for(fake.port)
    await link.connect()
    reply = Reply.from_frame("player list", await link.send("player list"))
    assert reply.ok
    assert reply.value == [{"id": 1, "username": "Tester"}]
    await link.aclose()


async def test_bad_token_is_refused(fake):
    link, _ = link_for(fake.port, token="BADTOKEN")
    with pytest.raises(ConsolePermissionsDenied, match="rejected the token"):
        await link.connect()


async def test_server_down_reads_clearly():
    link, _ = link_for(free_port())
    with pytest.raises(Error, match="cannot reach the console"):
        await link.connect()


def test_missing_token_file_explains(tmp_path):
    with pytest.raises(ConsoleCreateFailedException, match="Start the game server once"):
        read_token(tmp_path / "nope.txt")


def test_empty_token_file(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("  \r\n", encoding="utf-8")
    with pytest.raises(ConsoleCreateFailedException, match="is empty"):
        read_token(path)


def test_no_token_file_at_all():
    with pytest.raises(ConsoleCreateFailedException, match="No console token"):
        read_token(None)


def test_token_file_bom_and_newline(tmp_path):
    path = tmp_path / "bom.txt"
    path.write_bytes(b"\xef\xbb\xbfgood.token\r\n")
    assert read_token(path) == "good.token"


async def test_first_command_is_the_priming_round_trip(fake):
    link, names = link_for(fake.port, wanted=["PlayerKilled"])
    sent = []
    original = link.send

    async def spy(command, timeout=None, on_late=None):
        sent.append(command)
        return await original(command, timeout, on_late)

    link.send = spy
    await link.connect()
    await wait_for(lambda: "websocket subscribe PlayerKilled" in sent)
    assert sent[0] == "websocket subscriptions"
    await link.aclose()


async def test_subscribe_and_emit(fake):
    seen = []
    link, _ = link_for(fake.port, wanted=["PlayerKilled"], seen=seen)
    await link.connect()
    await wait_for(lambda: "PlayerKilled" in link._server_subs)
    await asyncio.sleep(0.1)
    subs = Reply.from_frame("websocket subscriptions", await link.send("websocket subscriptions"))
    assert "PlayerKilled" in subs.value
    await link.send('__emit PlayerKilled {"killedPlayer":{"username":"Tester"}}')
    await wait_for(lambda: seen)
    assert seen[0]["name"] == "PlayerKilled"
    assert seen[0]["source"] == "feed"
    assert seen[0]["timestamp"]
    assert seen[0]["data"]["killedPlayer"]["username"] == "Tester"
    await link.aclose()


async def test_subscribe_reports_the_outcome(fake):
    link, _ = link_for(fake.port)
    await link.connect()
    assert await link.subscribe("PlayerKilled") == "Success"
    assert await link.unsubscribe("PlayerKilled") == "Success"
    await link.aclose()


async def test_awkward_text_round_trips(fake):
    seen = []
    link, _ = link_for(fake.port, wanted=["Echo"], seen=seen)
    await link.connect()
    await wait_for(lambda: "Echo" in link._server_subs)
    await asyncio.sleep(0.2)
    payload = {"text": 'say "hi" \\ back\nnext line'}
    await link.send("__emit Echo " + json.dumps(payload))
    await wait_for(lambda: seen)
    assert seen[0]["data"] == payload
    await link.aclose()


async def test_no_reply_times_out_loudly(fake):
    link, _ = link_for(fake.port, command_timeout=0.3)
    await link.connect()
    with pytest.raises(CommandTimeoutException, match=r"timed out after 0.3s: __noreply"):
        await link.send("__noreply")
    await link.aclose()


async def test_late_reply_after_timeout_is_ignored(fake):
    link, _ = link_for(fake.port)
    await link.connect()
    with pytest.raises(CommandTimeoutException):
        await link.send("__delay 400", 0.1)
    await asyncio.sleep(0.5)
    reply = Reply.from_frame("x", await link.send("hello"))
    assert reply.text == "ok"
    await link.aclose()


async def test_drop_fails_every_pending_command(fake):
    link, _ = link_for(fake.port)
    await link.connect()
    pending = [asyncio.ensure_future(link.send("__noreply")) for _ in range(3)]
    await asyncio.sleep(0.1)
    with pytest.raises((ConsoleClosedException, Error)):
        await link.send("__drop")
    results = await asyncio.gather(*pending, return_exceptions=True)
    assert all(isinstance(r, ConsoleClosedException) for r in results)
    assert link.closed
    with pytest.raises(ConsoleNotConnectedException, match="not connected"):
        await link.send("player list")


async def test_both_failure_shapes_read_as_failures(fake):
    link, _ = link_for(fake.port)
    await link.connect()
    one = Reply.from_frame("__fail", await link.send("__fail boom one"))
    two = Reply.from_frame("__failstr", await link.send("__failstr boom two"))
    assert not one.ok and one.error == "boom one"
    assert not two.ok and two.error == "System.Exception: boom two"
    assert Reply.from_frame("hello", await link.send("hello")).text == "ok"
    await link.aclose()


async def test_settle_window_reissues_subscriptions(fake):
    link, _ = link_for(fake.port, wanted=["PlayerKilled"], settle_delays=(0.3,))
    await link.connect()
    await asyncio.sleep(0.15)
    sent = []
    original = link.send

    async def spy(command, timeout=None, on_late=None):
        sent.append(command)
        return await original(command, timeout, on_late)

    link.send = spy
    await asyncio.sleep(0.4)
    assert "websocket subscribe PlayerKilled" in sent
    await link.aclose()


async def test_wait_closed_returns_after_close(fake):
    link, _ = link_for(fake.port)
    await link.connect()
    asyncio.get_running_loop().call_later(0.1, link.close)
    await asyncio.wait_for(link.wait_closed(), 2)
    assert not link.connected
