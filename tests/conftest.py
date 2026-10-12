import asyncio

import pytest

from .fake import FakeConsole, available
from .mock_console import MockConsole

FAST = {"retry": 0.2, "settle_delays": (), "poll_grace": 0.2, "poll_interval": 0.1, "command_timeout": 5.0,
        "auth_timeout": 1.0, "retry_subscribe": 0.3, "resend_unanswered": 0.6}


async def wait_for(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "console_token.txt"
    path.write_text("good.token.sig", encoding="utf-8")
    return path


@pytest.fixture
async def fake():
    if not available():
        pytest.skip("no fake console: set TAVERN_FAKE_CONSOLE to its server.mjs (needs node and ws)")
    console = await FakeConsole().start()
    yield console
    await console.stop()


@pytest.fixture
async def mock():
    console = await MockConsole().start()
    yield console
    await console.stop()
