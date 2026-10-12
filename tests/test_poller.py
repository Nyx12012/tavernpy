import asyncio

from tavernpy import Console

from .conftest import FAST, wait_for


def console_for(mock, token_file, **kw):
    return Console("127.0.0.1", mock.port, token_file=str(token_file), **{**FAST, "poll_join_leave": True, **kw})


async def test_no_polling_without_join_leave_listeners(mock, token_file):
    console = console_for(mock, token_file)
    console.on("PlayerKilled", lambda e: None)
    await console.connect(timeout=5)
    await asyncio.sleep(0.5)
    assert mock.count("player list") == 0
    await console.aclose()


async def test_poll_synthesizes_join_and_leave(mock, token_file):
    console = console_for(mock, token_file)
    seen = []
    console.on("PlayerJoined", seen.append)
    console.on("PlayerLeft", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.count("player list") >= 2)
    mock.players.append({"id": 1003, "username": "carol"})
    mock.players = [p for p in mock.players if p["username"] != "Alice"]
    await wait_for(lambda: len(seen) == 2)
    by_type = {e.type: e for e in seen}
    assert by_type["PlayerJoined"].data == {"user": {"username": "carol", "id": 1003}}
    assert by_type["PlayerLeft"].data == {"user": {"username": "Alice", "id": 1001}}
    assert all(e.source == "poll" for e in seen)
    await console.aclose()


async def test_first_poll_is_a_baseline(mock, token_file):
    console = console_for(mock, token_file)
    seen = []
    console.on("PlayerJoined", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.count("player list") >= 3)
    await asyncio.sleep(0.3)
    assert seen == []
    await console.aclose()


async def test_feed_event_switches_the_poller_off(mock, token_file):
    console = console_for(mock, token_file)
    seen = []
    console.on("PlayerJoined", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerJoined") and mock.count("player list") >= 1)
    await mock.push("PlayerJoined", {"user": {"username": "Dave", "id": 9}})
    await wait_for(lambda: seen)
    assert seen[0].source == "feed"
    assert console.feed_live
    await asyncio.sleep(0.3)
    polls = mock.count("player list")
    await asyncio.sleep(0.4)
    assert mock.count("player list") == polls
    await console.aclose()


async def test_feed_and_poll_report_a_join_once(mock, token_file):
    console = console_for(mock, token_file, poll_grace=0.6)
    seen = []
    console.on("PlayerJoined", seen.append)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.subscribed("PlayerJoined") and mock.count("player list") >= 2)
    mock.players.append({"id": 1003, "username": "carol"})
    await wait_for(lambda: mock.count("player list") >= 4)
    await mock.push("PlayerJoined", {"user": {"username": "carol", "id": 1003}})
    await asyncio.sleep(1.0)
    assert [e.source for e in seen] == ["feed"]
    await console.aclose()


async def test_polling_starts_when_a_listener_is_added_later(mock, token_file):
    console = console_for(mock, token_file)
    await console.connect(timeout=5)
    await asyncio.sleep(0.3)
    assert mock.count("player list") == 0
    console.on("PlayerLeft", lambda e: None)
    await wait_for(lambda: mock.count("player list") >= 1)
    await console.aclose()


async def test_polling_stops_when_the_listener_goes(mock, token_file):
    console = console_for(mock, token_file)
    console.on("PlayerJoined", lambda e: None)
    await console.connect(timeout=5)
    await wait_for(lambda: mock.count("player list") >= 1)
    await console.unsubscribe("PlayerJoined")
    await asyncio.sleep(0.3)
    polls = mock.count("player list")
    await asyncio.sleep(0.4)
    assert mock.count("player list") == polls
    await console.aclose()


async def test_polling_is_off_by_default(mock, token_file):
    console = Console("127.0.0.1", mock.port, token_file=str(token_file), **FAST)
    console.on("PlayerJoined", lambda e: None)
    await console.connect(timeout=5)
    await asyncio.sleep(0.5)
    assert mock.count("player list") == 0
    await console.aclose()


async def test_subscribe_alone_also_polls(mock, token_file):
    console = console_for(mock, token_file)
    seen = []
    console.on("event", seen.append)
    await console.subscribe("PlayerJoined")
    await console.connect(timeout=5)
    await wait_for(lambda: mock.count("player list") >= 2)
    mock.players.append({"id": 5, "username": "Eve"})
    await wait_for(lambda: seen)
    assert seen[0].type == "PlayerJoined" and seen[0].source == "poll"
    await console.aclose()
