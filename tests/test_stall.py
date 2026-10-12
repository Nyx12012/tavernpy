import asyncio
import logging

import pytest

from tavernpy import CommandTimeoutException, Console

from .conftest import FAST, wait_for


def console_for(port, token_file, **kw):
    return Console("127.0.0.1", port, token_file=str(token_file), **{**FAST, **kw})


def warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


async def test_a_stalled_server_answers_late_and_nothing_piles_up(mock, token_file, caplog):
    caplog.set_level(logging.INFO, logger="tavernpy")
    console = console_for(mock.port, token_file, command_timeout=0.3, resend_unanswered=60)
    seen = []
    await console.connect(timeout=5)
    mock.hold = True
    console.on("PlayerKilled", seen.append)
    console.on("PlayerLeft", seen.append)
    await asyncio.sleep(1.5)
    assert mock.count("websocket subscribe PlayerKilled") == 1
    assert mock.count("websocket subscribe PlayerLeft") == 1
    assert len(warnings(caplog)) == 1
    assert "No answer from" in warnings(caplog)[0]
    await mock.release()
    await wait_for(lambda: "answering commands again" in caplog.text)
    assert console._link._unanswered == {}
    await mock.push("PlayerKilled", {"killedPlayer": {"username": "Alice"}})
    await wait_for(lambda: seen)
    await asyncio.sleep(0.5)
    assert mock.count("websocket subscribe PlayerKilled") == 1
    assert len(warnings(caplog)) == 1
    await console.aclose()


async def test_a_late_not_found_is_kept(mock, token_file):
    console = console_for(mock.port, token_file, command_timeout=0.3, resend_unanswered=60)
    await console.connect(timeout=5)
    mock.hold = True
    console.on("NopeEvent", lambda e: None)
    await asyncio.sleep(0.6)
    await mock.release()
    await wait_for(lambda: "NopeEvent" in console._link._not_found)
    await asyncio.sleep(0.8)
    assert mock.count("websocket subscribe NopeEvent") == 1
    await console.aclose()


async def test_a_command_that_timed_out_may_still_run(mock, token_file):
    console = console_for(mock.port, token_file)
    await console.connect(timeout=5)
    mock.hold = True
    with pytest.raises(CommandTimeoutException, match="may still run it later"):
        await console.command("player message Alice hi", timeout=0.2)
    await mock.release()
    assert mock.count("player message Alice hi") == 1
    assert (await console.command("player list")).ok
    assert console.connected
    await console.aclose()


async def test_settle_reissue_skips_a_subscribe_still_waiting(mock, token_file):
    console = console_for(mock.port, token_file, command_timeout=0.2, resend_unanswered=60,
                          settle_delays=(0.4, 0.9, 1.1))
    console.on("PlayerKilled", lambda e: None)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerKilled"))
    mock.hold = True
    await asyncio.sleep(1.5)
    assert mock.count("websocket subscribe PlayerKilled") == 2
    await mock.release()
    await console.aclose()
