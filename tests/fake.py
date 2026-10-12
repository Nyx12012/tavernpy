import asyncio
import os
import shutil
import socket
from pathlib import Path

HERE = Path(__file__).resolve()


def server_path():
    env = os.environ.get("TAVERN_FAKE_CONSOLE")
    if env:
        return Path(env)
    for parent in HERE.parents:
        candidate = parent / "tooling" / "tavern-fake-console" / "server.mjs"
        if candidate.exists():
            return candidate
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def available() -> bool:
    server = server_path()
    if shutil.which("node") is None or server is None or not server.exists():
        return False
    return (server.parent / "node_modules" / "ws").exists()


class FakeConsole:
    def __init__(self, port=None):
        self.port = port or free_port()
        self.proc = None

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            shutil.which("node"), str(server_path()), "--port", str(self.port),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        line = await asyncio.wait_for(self.proc.stdout.readline(), 10)
        if b"listening" not in line:
            raise RuntimeError("fake console did not start: " + line.decode(errors="replace"))
        return self

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        self.proc = None
