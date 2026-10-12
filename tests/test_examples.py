import asyncio
import json
import os
import sys
from pathlib import Path

from .conftest import wait_for

ROOT = Path(__file__).resolve().parents[1]


async def launch(script, mock, token_file, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT), TAVERN_HOST="127.0.0.1", TAVERN_PORT=str(mock.port),
               TAVERN_TOKEN_FILE=str(token_file), TAVERN_WELCOME_DELAY="0.5")
    return await asyncio.create_subprocess_exec(
        sys.executable, str(ROOT / "examples" / script), *args, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)


async def stop(proc):
    if proc.returncode is None:
        proc.kill()
        await proc.wait()


async def test_bot_welcomes_a_player_a_little_after_they_join(mock, token_file):
    proc = await launch("bot.py", mock, token_file)
    welcome = 'player message "carol" "Welcome to the server!"'
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), 15)
        assert line.decode().strip() == "Online: Alice, Bob Smith"
        await wait_for(lambda: mock.subscribed("PlayerJoined"), timeout=10)
        await mock.push("PlayerJoined", {"user": {"username": "carol", "id": 7}, "mode": "OpenVR", "position": [0, 0, 0]})
        assert (await asyncio.wait_for(proc.stdout.readline(), 10)).decode().strip() == "carol joined"
        assert welcome not in mock.received
        await wait_for(lambda: welcome in mock.received, timeout=10)
        assert (await asyncio.wait_for(proc.stdout.readline(), 10)).decode().strip() == "Welcomed carol ok"
    finally:
        await stop(proc)


async def test_event_logger_writes_json_lines(mock, token_file, tmp_path):
    out = tmp_path / "events.jsonl"
    proc = await launch("event_logger.py", mock, token_file, str(out))
    try:
        await wait_for(lambda: mock.subscribed("InfoLog") and mock.subscribed("PlayerKilled"), timeout=15)
        assert not mock.subscribed("PlayerMovedChunk")
        assert not mock.subscribed("TraceLog")
        await mock.push("InfoLog", "Server started")
        await mock.push("PlayerKilled", {"killedPlayer": {"username": "Alice"}})
        await wait_for(lambda: out.exists() and len(out.read_text(encoding="utf-8").splitlines()) == 2, timeout=10)
        lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        assert [x["type"] for x in lines] == ["InfoLog", "PlayerKilled"]
        assert lines[0]["data"] == "Server started"
        assert lines[1]["source"] == "feed"
    finally:
        await stop(proc)
