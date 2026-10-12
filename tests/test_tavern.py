import asyncio

import pytest

from tavernpy import (Console, ConsoleAlreadyCreatedException, ConsoleNotConnectedException,
                      ConsolePermissionsDenied, ConsoleTimeoutException, Tavern)

from .conftest import FAST, wait_for
from .fake import free_port
from .mock_console import MockConsole


@pytest.fixture
async def tavern():
    t = Tavern(**FAST)
    yield t
    await t.aclose()


async def test_create_console_and_send_command(tavern, mock, token_file):
    console = await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    assert isinstance(console, Console)
    assert console.server_id == 1
    reply = await tavern.send_command_console(1, "player list")
    assert reply.ok and [p["username"] for p in reply.result] == ["Alice", "Bob Smith"]
    assert tavern.get_active_consoles() == {1: console}
    assert tavern.get_console(1) is console


async def test_create_console_twice_is_refused(tavern, mock, token_file):
    await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file))
    with pytest.raises(ConsoleAlreadyCreatedException):
        await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file))


async def test_create_console_in_background(tavern, mock, token_file):
    await tavern.create_console("a", "127.0.0.1", mock.port, token_file=str(token_file))
    assert tavern.get_active_consoles() == {}
    await tavern.wait_for_ready("a", timeout=5)
    assert list(tavern.get_active_consoles()) == ["a"]


async def test_ensure_open_timeout_removes_the_console(tavern, token_file):
    with pytest.raises(ConsoleTimeoutException):
        await tavern.create_console(1, "127.0.0.1", free_port(), token_file=str(token_file), ensure_open=True,
                                    timeout=0.5)
    assert tavern.get_console(1) is None


async def test_ensure_open_refused_token(tavern, mock):
    with pytest.raises(ConsolePermissionsDenied):
        await tavern.create_console(1, "127.0.0.1", mock.port, token="wrong", ensure_open=True, timeout=5)
    assert tavern.get_console(1) is None


async def test_unknown_server_id(tavern):
    with pytest.raises(ConsoleNotConnectedException, match="no console created for server 9"):
        await tavern.send_command_console(9, "player list")
    with pytest.raises(ConsoleNotConnectedException):
        await tavern.console_sub("PlayerKilled", lambda e: None, server_id=9)


async def test_console_sub_one_server(tavern, mock, token_file):
    await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    seen = []

    async def killed(event):
        seen.append((event.server_id, event.data))

    assert await tavern.console_sub("PlayerKilled", killed, server_id=1) == {1: "Success"}
    assert mock.subscribed("PlayerKilled")
    await mock.push("PlayerKilled", {"killedPlayer": {"username": "Alice"}})
    await wait_for(lambda: seen)
    assert seen == [(1, {"killedPlayer": {"username": "Alice"}})]
    assert tavern.get_console_subs() == {1: {"PlayerKilled": [killed]}}


async def test_console_sub_everywhere_covers_later_consoles(tavern, token_file):
    one, two = await MockConsole().start(), await MockConsole().start()
    try:
        seen = []
        await tavern.create_console("one", "127.0.0.1", one.port, token_file=str(token_file), ensure_open=True)
        await tavern.console_sub("playerkilled", seen.append)
        await tavern.create_console("two", "127.0.0.1", two.port, token_file=str(token_file), ensure_open=True)
        await wait_for(lambda: one.subscribed("PlayerKilled") and two.subscribed("PlayerKilled"))
        await one.push("PlayerKilled", {})
        await two.push("PlayerKilled", {})
        await wait_for(lambda: len(seen) == 2)
        assert sorted(e.server_id for e in seen) == ["one", "two"]
    finally:
        await tavern.aclose()
        await one.stop()
        await two.stop()


async def test_console_unsub(tavern, mock, token_file):
    await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    await tavern.console_sub("PlayerKilled", lambda e: None)
    assert await tavern.console_unsub("PlayerKilled") == {1: "Success"}
    assert not mock.subscribed("PlayerKilled")
    assert tavern.get_console_subs() == {}
    await tavern.create_console(2, "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    assert tavern.get_console(2).subscriptions == []


async def test_console_sub_before_connect_is_sent_on_connect(tavern, mock, token_file):
    await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file))
    await tavern.console_sub("ObjectKilled", lambda e: None, server_id=1)
    await tavern.wait_for_ready(timeout=5)
    await wait_for(lambda: mock.subscribed("ObjectKilled"))


async def test_wait_for_ready_times_out(tavern, token_file):
    await tavern.create_console(1, "127.0.0.1", free_port(), token_file=str(token_file))
    with pytest.raises(ConsoleTimeoutException):
        await tavern.wait_for_ready(timeout=0.3)


async def test_remove_console(tavern, mock, token_file):
    console = await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    await tavern.remove_console(1)
    assert tavern.get_console(1) is None
    assert not console.connected


async def test_run_returns_after_close(mock, token_file):
    tavern = Tavern(**FAST)
    console = await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file), ensure_open=True)
    asyncio.get_running_loop().call_later(0.2, tavern.close)
    await asyncio.wait_for(tavern.run(), 3)
    assert not console.connected
    await tavern.aclose()


async def test_async_context_manager(mock, token_file):
    async with Tavern(**FAST) as tavern:
        console = await tavern.create_console(1, "127.0.0.1", mock.port, token_file=str(token_file),
                                              ensure_open=True)
        assert console.connected
    assert not console.connected


async def test_aclose_closes_every_socket(token_file):
    one, two = await MockConsole().start(), await MockConsole().start()
    tavern = Tavern(**FAST)
    try:
        await tavern.create_console(1, "127.0.0.1", one.port, token_file=str(token_file), ensure_open=True)
        await tavern.create_console(2, "127.0.0.1", two.port, token_file=str(token_file), ensure_open=True)
        await tavern.aclose()
        await wait_for(lambda: not one.subscriptions and not two.subscriptions)
        assert tavern.get_active_consoles() == {}
    finally:
        await one.stop()
        await two.stop()
