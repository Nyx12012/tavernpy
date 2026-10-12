import asyncio
import logging

import pytest

import tavernpy

from tavernpy import (BadPlayerNameException, CommandFailedException, Console, ConsoleCreateFailedException,
                      ConsoleNotConnectedException, ConsolePermissionsDenied, ConsoleTimeoutException, Player)

from .conftest import FAST, wait_for
from .fake import FakeConsole, available, free_port
from .mock_console import MockConsole


def console_for(port, token_file, **kw):
    return Console("127.0.0.1", port, token_file=str(token_file), **{**FAST, **kw})


async def test_connect_command_and_close(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    assert console.connected
    reply = await console.command("player list")
    assert reply.ok
    assert reply.result_string == ""
    await console.aclose()
    assert not console.connected


async def test_token_string_instead_of_file(mock):
    console = Console("127.0.0.1", mock.port, token="good.token.sig", **FAST)
    await console.connect(timeout=5)
    assert console.connected
    await console.aclose()


async def test_async_context_manager(mock, token_file):
    async with console_for(mock.port, token_file) as console:
        assert (await console.command("player list")).ok
    assert not console.connected


async def test_refused_token_raises_and_stops(mock, tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("wrong", encoding="utf-8")
    console = console_for(mock.port, bad)
    rejected = []
    console.on("rejected", rejected.append)
    with pytest.raises(ConsolePermissionsDenied):
        await console.connect(timeout=5)
    assert rejected == [console]
    tries = len([c for c in mock.received])
    await asyncio.sleep(0.5)
    assert len(mock.received) == tries
    assert console._keeper is None


async def test_missing_token_file_raises(tmp_path):
    console = Console("127.0.0.1", free_port(), token_file=str(tmp_path / "nope.txt"), **FAST)
    with pytest.raises(ConsoleCreateFailedException, match="Start the game server once"):
        await console.connect(timeout=5)


def test_no_host_is_refused():
    with pytest.raises(ConsoleCreateFailedException):
        Console("")


async def test_down_server_times_out(token_file):
    console = console_for(free_port(), token_file)
    with pytest.raises(ConsoleTimeoutException, match="after 0.5s"):
        await console.connect(timeout=0.5)
    assert console._keeper is None


async def test_connect_waits_for_a_server_that_comes_up(token_file):
    port = free_port()
    console = console_for(port, token_file)
    mock = MockConsole()
    task = asyncio.ensure_future(console.connect(timeout=8))
    await asyncio.sleep(0.5)
    assert not task.done()
    await mock.start(port)
    try:
        await task
        assert console.connected
    finally:
        await console.aclose()
        await mock.stop()


async def test_command_while_disconnected_raises(token_file):
    console = console_for(free_port(), token_file)
    with pytest.raises(ConsoleNotConnectedException, match="not connected"):
        await console.command("player list")


async def test_on_decorator_sync_and_async_handlers(mock, token_file):
    console = console_for(mock.port, token_file)
    seen = []

    @console.on("PlayerKilled")
    def sync_handler(event):
        seen.append(("sync", event.type, event.data))

    @console.on("PlayerKilled")
    async def async_handler(event):
        seen.append(("async", event.type, event.data))

    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    await mock.push("PlayerKilled", {"killedPlayer": {"username": "Alice"}})
    await wait_for(lambda: len(seen) == 2)
    assert ("sync", "PlayerKilled", {"killedPlayer": {"username": "Alice"}}) in seen
    assert ("async", "PlayerKilled", {"killedPlayer": {"username": "Alice"}}) in seen
    await console.aclose()


async def test_event_fields(mock, token_file):
    console = console_for(mock.port, token_file, server_id="home")
    seen = []
    console.on("PlayerStateChanged", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerStateChanged"))
    await mock.push("PlayerStateChanged", {"user": {"username": "Alice"}, "state": "Dead"})
    await wait_for(lambda: seen)
    event = seen[0]
    assert event.type == "PlayerStateChanged"
    assert event.source == "feed"
    assert event.server_id == "home"
    assert event.timestamp
    await console.aclose()


async def test_on_after_connect_subscribes_now(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    seen = []
    console.on("TradeDeckUsed", seen.append)
    await wait_for(lambda: mock.subscribed("TradeDeckUsed"))
    await mock.push("TradeDeckUsed", {"item": 1})
    await wait_for(lambda: seen)
    await console.aclose()


async def test_event_names_match_in_any_case(mock, token_file):
    console = console_for(mock.port, token_file)
    seen = []
    console.on("playerkilled", seen.append)
    assert console.subscriptions == ["PlayerKilled"]
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    await mock.push("PlayerKilled", {})
    await wait_for(lambda: seen)
    await console.aclose()


async def test_unknown_mod_event_passes_through(mock, token_file):
    console = console_for(mock.port, token_file)
    seen = []
    console.on("MyModEvent", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("MyModEvent"))
    await mock.push("MyModEvent", {"x": 1})
    await wait_for(lambda: seen)
    assert seen[0].type == "MyModEvent"
    await console.aclose()


async def test_catch_all_event_handler(mock, token_file):
    console = console_for(mock.port, token_file)
    seen = []
    console.on("event", seen.append)
    await console.subscribe("InfoLog")
    await console.subscribe("ErrorLog")
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("InfoLog") and mock.subscribed("ErrorLog"))
    await mock.push("InfoLog", "hello")
    await mock.push("ErrorLog", "bad")
    await wait_for(lambda: len(seen) == 2)
    assert [e.type for e in seen] == ["InfoLog", "ErrorLog"]
    await console.aclose()


async def test_subscribe_returns_the_outcome(mock, token_file):
    console = console_for(mock.port, token_file)
    assert await console.subscribe("PlayerKilled") is None
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    assert await console.subscribe("PlayerKilled") == "Unchanged"
    assert await console.subscribe("ObjectKilled") == "Success"
    await console.aclose()


async def test_subscribe_numeric_enum_outcomes(token_file):
    mock = await MockConsole(enum_as_number=True).start()
    console = console_for(mock.port, token_file)
    try:
        await console.connect(timeout=5)
        assert await console.subscribe("PlayerKilled") == "Success"
        assert await console.subscribe("PlayerKilled") == "Unchanged"
        assert await console.subscribe("NopeEvent") == "NotFound"
    finally:
        await console.aclose()
        await mock.stop()


async def test_not_found_is_logged(mock, token_file, caplog):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    with caplog.at_level("WARNING", logger="tavernpy"):
        assert await console.subscribe("NopeEvent") == "NotFound"
    assert any("NotFound" in r.getMessage() for r in caplog.records)
    await console.aclose()


async def test_lifecycle_name_cannot_be_subscribed():
    console = Console("127.0.0.1", token="x")
    with pytest.raises(ValueError, match="lifecycle"):
        await console.subscribe("ready")


async def test_unsubscribe_drops_handlers_and_tells_the_server(mock, token_file):
    console = console_for(mock.port, token_file)
    seen = []
    console.on("PlayerKilled", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    assert await console.unsubscribe("PlayerKilled") == "Success"
    assert "websocket unsubscribe PlayerKilled" in mock.received
    assert console.subscriptions == []
    assert not mock.subscribed("PlayerKilled")
    await console.aclose()


async def test_off_removes_one_handler(mock, token_file):
    console = console_for(mock.port, token_file)
    one, two = [], []
    console.on("PlayerKilled", one.append)
    console.on("PlayerKilled", two.append)
    console.off("PlayerKilled", one.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    await mock.push("PlayerKilled", {})
    await wait_for(lambda: two)
    await asyncio.sleep(0.1)
    assert one == []
    await console.aclose()


async def test_handler_exception_is_logged_not_fatal(mock, token_file, caplog):
    console = console_for(mock.port, token_file)
    seen = []

    def broken(event):
        raise RuntimeError("handler blew up")

    console.on("PlayerKilled", broken)
    console.on("PlayerKilled", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    with caplog.at_level(logging.ERROR, logger="tavernpy"):
        await mock.push("PlayerKilled", {})
        await wait_for(lambda: seen)
    assert any("Handler" in r.getMessage() for r in caplog.records)
    assert console.connected
    await console.aclose()


async def test_ready_and_close_handlers(mock, token_file):
    console = console_for(mock.port, token_file)
    states = []
    console.on("ready", lambda c: states.append("ready"))
    console.on("close", lambda c: states.append("close"))
    await console.connect(timeout=5)
    assert states == ["ready"]
    await console.aclose()
    assert states == ["ready", "close"]


async def test_reconnects_after_a_drop_and_resubscribes(mock, token_file):
    console = console_for(mock.port, token_file)
    states, seen = [], []
    console.on("ready", lambda c: states.append("ready"))
    console.on("close", lambda c: states.append("close"))
    console.on("PlayerKilled", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    await mock.drop_all()
    await wait_for(lambda: states == ["ready", "close", "ready"], timeout=8)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    await mock.push("PlayerKilled", {"n": 2})
    await wait_for(lambda: seen)
    assert seen[0].data == {"n": 2}
    await console.aclose()


async def test_reconnects_across_a_server_restart(token_file):
    mock = await MockConsole().start()
    port = mock.port
    console = console_for(port, token_file)
    seen = []
    console.on("PlayerKilled", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    await mock.stop()
    await wait_for(lambda: not console.connected, timeout=8)
    mock = await MockConsole().start(port)
    try:
        await wait_for(lambda: console.connected, timeout=10)
        await wait_for(lambda: mock.subscribed("PlayerKilled"))
        await mock.push("PlayerKilled", {})
        await wait_for(lambda: seen)
    finally:
        await console.aclose()
        await mock.stop()


async def test_fake_console_restart_resumes_events(token_file):
    if not available():
        pytest.skip("no fake console: set TAVERN_FAKE_CONSOLE to its server.mjs (needs node and ws)")
    fake = await FakeConsole().start()
    console = console_for(fake.port, token_file)
    seen = []
    console.on("PlayerKilled", seen.append)
    try:
        await console.connect(timeout=5)
        await wait_for(lambda: console._link is not None and "PlayerKilled" in console._link._server_subs)
        await asyncio.sleep(0.2)
        await console.command("__emit PlayerKilled {}")
        await wait_for(lambda: len(seen) == 1)
        await fake.stop()
        await wait_for(lambda: not console.connected, timeout=8)
        await asyncio.sleep(0.5)
        await fake.start()
        await wait_for(lambda: console.connected, timeout=12)
        await wait_for(lambda: console._link is not None and "PlayerKilled" in console._link._server_subs)
        await asyncio.sleep(0.3)
        await console.command("__emit PlayerKilled {}")
        await wait_for(lambda: len(seen) == 2)
    finally:
        await console.aclose()
        await fake.stop()


async def test_down_server_is_logged_once(token_file, caplog):
    console = console_for(free_port(), token_file, retry=0.05)
    with caplog.at_level("ERROR", logger="tavernpy"):
        console.start()
        await wait_for(lambda: any("Cannot connect" in r.getMessage() for r in caplog.records), timeout=10)
        await asyncio.sleep(2.5)
        await console.aclose()
    assert len([r for r in caplog.records if "Cannot connect" in r.getMessage()]) == 1


async def test_wait_for_ready(mock, token_file):
    console = console_for(mock.port, token_file)
    console.start()
    await console.wait_for_ready(timeout=5)
    assert console.connected
    await console.aclose()


async def test_run_returns_after_close(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    asyncio.get_running_loop().call_later(0.2, console.close)
    await asyncio.wait_for(console.run(), 3)


async def test_players_reads_the_roster(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    assert await console.players() == [Player("Alice", 1001), Player("Bob Smith", 1002)]
    mock.players = []
    assert await console.players() == []
    await console.aclose()


async def test_players_raises_when_the_command_fails(mock, token_file, monkeypatch):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    monkeypatch.setattr(mock, "handle", lambda ws, content: {
        "Command": None, "ResultString": None, "Exception": {"ClassName": "System.Exception", "Message": "nope"}})
    with pytest.raises(CommandFailedException, match="nope"):
        await console.players()
    await console.aclose()


async def test_message_quotes_the_name_and_cleans_the_text(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    reply = await console.message("Bob Smith", 'Hi "there"\n\\all')
    assert reply.ok
    assert mock.received[-1] == 'player message "Bob Smith" "Hi thereall"'
    await console.aclose()


async def test_message_refuses_bad_names_and_empty_text(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    with pytest.raises(BadPlayerNameException):
        await console.message('Bob"; kick', "hi")
    with pytest.raises(ValueError, match="empty"):
        await console.message("Alice", '\n"\\')
    await console.aclose()


async def test_refused_command_reads_as_a_failure(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    reply = await console.command("nonsense here")
    assert not reply.ok
    assert reply.error == "System.Exception: [CommandService.Handle] Command not found"
    with pytest.raises(CommandFailedException, match=r"nonsense here: System.Exception: \[CommandService.Handle\]"):
        reply.check()
    await console.aclose()


def test_repr_and_label():
    console = Console("10.0.0.1", 1760, token="x")
    assert console.label == "10.0.0.1:1760"
    assert repr(console) == "<Console 10.0.0.1:1760 disconnected>"
    assert Console("h", token="x", server_id=7).label == "7"


def test_default_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    console = Console("h")
    assert console.token_file.endswith("console_token.txt")
    assert "TheModdingTavern" in console.token_file


async def test_refusal_seen_in_the_background_raises_at_once(mock, tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("wrong", encoding="utf-8")
    console = console_for(mock.port, bad, retry=30)
    console.start()
    await wait_for(lambda: console._fatal is not None)
    with pytest.raises(ConsolePermissionsDenied):
        await asyncio.wait_for(console.connect(timeout=5), 1)


async def test_a_second_refused_connect_fires_rejected_again(mock, tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("wrong", encoding="utf-8")
    console = console_for(mock.port, bad)
    rejected = []
    console.on("rejected", rejected.append)
    for _ in range(2):
        with pytest.raises(ConsolePermissionsDenied):
            await console.connect(timeout=5)
    assert len(rejected) == 2


async def test_cancelled_connect_stops_the_keeper(token_file):
    console = console_for(free_port(), token_file)
    task = asyncio.ensure_future(console.connect())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert console._keeper is None


async def test_close_cancels_running_handlers(mock, token_file):
    console = console_for(mock.port, token_file)
    started, finished = [], []

    async def slow(event):
        started.append(1)
        await asyncio.sleep(5)
        finished.append(1)

    console.on("PlayerKilled", slow)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    await mock.push("PlayerKilled", {})
    await wait_for(lambda: started)
    await console.aclose()
    await asyncio.sleep(0.1)
    assert finished == [] and not console._tasks


async def test_silent_refusal_reads_as_refused(mock, tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("nottherealtoken", encoding="utf-8")
    console = console_for(mock.port, bad, auth_timeout=0.5)
    with pytest.raises(ConsolePermissionsDenied, match="did not accept the token within 0.5s"):
        await console.connect(timeout=5)


async def test_spoken_refusal_still_reads_as_refused(tmp_path):
    mock = await MockConsole(refusal="invalid").start()
    bad = tmp_path / "bad.txt"
    bad.write_text("wrong", encoding="utf-8")
    try:
        with pytest.raises(ConsolePermissionsDenied, match="rejected the token"):
            await console_for(mock.port, bad).connect(timeout=5)
    finally:
        await mock.stop()


async def test_unanswered_subscriptions_are_sent_again_after_a_while(mock, token_file):
    mock.drop_prefix = "websocket subscribe "
    console = console_for(mock.port, token_file, command_timeout=0.3)
    seen = []
    console.on("PlayerKilled", seen.append)
    await console.connect(timeout=5)
    await asyncio.sleep(0.5)
    assert not mock.subscribed("PlayerKilled")
    mock.drop_prefix = None
    await wait_for(lambda: mock.subscribed("PlayerKilled"), timeout=5)
    await mock.push("PlayerKilled", {})
    await wait_for(lambda: seen)
    await console.aclose()


async def test_not_found_is_not_retried_forever(mock, token_file):
    console = console_for(mock.port, token_file)
    console.on("NopeEvent", lambda e: None)
    await console.connect(timeout=5)
    await asyncio.sleep(1.2)
    assert mock.count("websocket subscribe NopeEvent") == 1
    await console.aclose()


async def test_subscriptions_list_has_the_live_shape(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    reply = await console.command("websocket subscriptions")
    assert reply.result_string == ""
    assert {item["name"] for item in reply.result} == set(tavernpy.EVENTS + tavernpy.LOG_EVENTS)
    await console.aclose()


async def test_ping_watchdog_reconnects_a_server_that_never_pongs(token_file):
    mock = await MockConsole(pong=False).start()
    console = console_for(mock.port, token_file, ping_interval=0.2)
    ready, closed = [], []
    console.on("ready", ready.append)
    console.on("close", closed.append)
    try:
        await console.connect(timeout=5)
        await wait_for(lambda: len(closed) >= 1 and len(ready) >= 2 and console.connected, timeout=5)
    finally:
        await console.aclose()
        await mock.stop()


async def test_ping_watchdog_keeps_a_server_that_pongs(mock, token_file):
    console = console_for(mock.port, token_file, ping_interval=0.1)
    closed = []
    console.on("close", closed.append)
    await console.connect(timeout=5)
    await asyncio.sleep(0.8)
    assert closed == []
    assert console.connected
    await console.aclose()


async def test_close_handler_tells_a_drop_from_a_close(mock, token_file):
    console = console_for(mock.port, token_file)
    seen = []
    console.on("close", lambda c: seen.append(c.closed))
    assert not console.closed
    await console.connect(timeout=5)
    await mock.drop_all()
    await wait_for(lambda: seen == [False])
    await wait_for(lambda: console.connected)
    await console.aclose()
    assert seen == [False, True]
    assert console.closed
