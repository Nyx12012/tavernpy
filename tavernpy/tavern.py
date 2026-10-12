import asyncio
import logging

from .console import DEFAULT_PORT, Console
from .errors import ConsoleAlreadyCreatedException, ConsoleNotConnectedException, Error
from .events import canonical

log = logging.getLogger("tavernpy")


class Tavern:
    def __init__(self, **console_options):
        self.console_options = console_options
        self._consoles = {}
        self._everywhere = {}
        self._done = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def create_console(self, server_id, host="127.0.0.1", port=DEFAULT_PORT, *, token=None, token_file=None,
                             ensure_open=False, timeout=10, **options) -> Console:
        if server_id in self._consoles:
            raise ConsoleAlreadyCreatedException(f"Console {server_id} is already created.")
        console = Console(host, port, token=token, token_file=token_file, server_id=server_id,
                          **{**self.console_options, **options})
        for name, callbacks in self._everywhere.items():
            for callback in callbacks:
                console.on(name, callback)
        self._consoles[server_id] = console
        if ensure_open:
            try:
                await console.connect(timeout)
            except Error:
                self._consoles.pop(server_id, None)
                raise
        else:
            console.start()
        return console

    def get_console(self, server_id):
        return self._consoles.get(server_id)

    def get_active_consoles(self):
        return {server_id: c for server_id, c in self._consoles.items() if c.connected}

    def get_console_subs(self):
        return {server_id: c.handlers for server_id, c in self._consoles.items() if c.handlers}

    def _targets(self, server_id):
        if server_id is None:
            return list(self._consoles.values())
        console = self._consoles.get(server_id)
        if console is None:
            raise ConsoleNotConnectedException(f"no console created for server {server_id}")
        return [console]

    async def send_command_console(self, server_id, content, timeout=None):
        return await self._targets(server_id)[0].command(content, timeout)

    async def console_sub(self, sub, callback, server_id=None):
        targets = self._targets(server_id)
        if server_id is None:
            callbacks = self._everywhere.setdefault(canonical(sub), [])
            if callback not in callbacks:
                callbacks.append(callback)
        outcomes = {}
        for console in targets:
            console._add_handler(sub, callback)
            try:
                outcomes[console.server_id] = await console.subscribe(sub)
            except Error as e:
                outcomes[console.server_id] = None
                log.warning("Subscribing to %s on %s failed for now, it is retried: %s", sub, console.label, e)
        return outcomes

    async def console_unsub(self, sub, server_id=None):
        targets = self._targets(server_id)
        if server_id is None:
            self._everywhere.pop(canonical(sub), None)
        outcomes = {}
        for console in targets:
            try:
                outcomes[console.server_id] = await console.unsubscribe(sub)
            except Error as e:
                outcomes[console.server_id] = None
                log.warning("Unsubscribing from %s on %s: %s", sub, console.label, e)
        return outcomes

    async def wait_for_ready(self, server_id=None, timeout=None):
        tasks = [asyncio.ensure_future(c.wait_for_ready(timeout)) for c in self._targets(server_id)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

    async def remove_console(self, server_id):
        console = self._consoles.pop(server_id, None)
        if console is not None:
            await console.aclose()

    async def run(self):
        await self._done.wait()

    def close(self):
        for console in self._consoles.values():
            console.close()
        self._done.set()

    async def aclose(self):
        await asyncio.gather(*(c.aclose() for c in self._consoles.values()))
        self._done.set()
