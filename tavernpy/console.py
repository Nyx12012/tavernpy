import asyncio
import inspect
import logging

from .errors import (ConsoleClosedException, ConsoleCreateFailedException, ConsoleNotConnectedException,
                     ConsolePermissionsDenied, ConsoleTimeoutException, Error)
from .events import JOIN_LEAVE, LIFECYCLE, Event, canonical
from .link import (AUTH_TIMEOUT, COMMAND_TIMEOUT, PING_INTERVAL, POLL_GRACE, POLL_INTERVAL, PRIME_TIMEOUT,
                   RESEND_UNANSWERED, RETRY_SUBSCRIBE, SETTLE_DELAYS, Link, default_token_file, read_token)
from .names import quoted_player, sanitise_console_text
from .reply import Reply
from .trace import Trace, trace_target

log = logging.getLogger("tavernpy")

DEFAULT_PORT = 1760
RETRY_SECONDS = 10.0


class Console:
    def __init__(self, host="127.0.0.1", port=DEFAULT_PORT, *, token=None, token_file=None, server_id=None,
                 retry=RETRY_SECONDS, command_timeout=COMMAND_TIMEOUT, poll_interval=POLL_INTERVAL,
                 poll_join_leave=False, auth_timeout=AUTH_TIMEOUT, ping_interval=PING_INTERVAL,
                 settle_delays=SETTLE_DELAYS, poll_grace=POLL_GRACE, prime_timeout=PRIME_TIMEOUT,
                 retry_subscribe=RETRY_SUBSCRIBE, resend_unanswered=RESEND_UNANSWERED, trace=None):
        if not host:
            raise ConsoleCreateFailedException("No host given for the console.")
        self.host = str(host)
        self.port = int(port)
        self.server_id = server_id
        self.token = token
        self.token_file = token_file if token is not None or token_file is not None else default_token_file()
        self.retry = retry
        self.command_timeout = command_timeout
        self._link_options = {
            "poll_interval": poll_interval, "poll_join_leave": poll_join_leave, "command_timeout": command_timeout,
            "auth_timeout": auth_timeout, "ping_interval": ping_interval, "settle_delays": settle_delays,
            "poll_grace": poll_grace, "prime_timeout": prime_timeout, "retry_subscribe": retry_subscribe,
            "resend_unanswered": resend_unanswered,
        }
        self._handlers = {}
        self._wanted = []
        self._link = None
        self._keeper = None
        self._closing = False
        self._done = asyncio.Event()
        self._waiters = set()
        self._tasks = set()
        self._last_error = None
        self._fatal = None
        self._up = False
        target = trace_target(trace)
        self._trace = Trace(target, self.label) if target is not None else None

    def __repr__(self):
        state = "connected" if self.connected else "disconnected"
        return f"<Console {self.label} {state}>"

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    @property
    def label(self) -> str:
        return str(self.server_id) if self.server_id is not None else f"{self.host}:{self.port}"

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        return self._link is not None and self._link.connected

    @property
    def closed(self) -> bool:
        return self._closing

    @property
    def feed_live(self) -> bool:
        return self._link is not None and self._link.feed_live

    @property
    def subscriptions(self):
        return self._wanted_names()

    @property
    def handlers(self):
        return {name: list(self._handlers.get(name, ())) for name in self._wanted_names()}

    def _wanted_names(self):
        names = list(self._wanted)
        for name, callbacks in self._handlers.items():
            if callbacks and name not in LIFECYCLE and name not in names:
                names.append(name)
        return names

    def _wants_join_leave(self) -> bool:
        return any(name in JOIN_LEAVE for name in self._wanted_names())

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Handler on %s failed", self.label, exc_info=task.exception())

    def _call(self, callback, *args):
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                self._spawn(result)
        except Exception:
            log.exception("Handler on %s failed", self.label)

    def _fire(self, name):
        for callback in list(self._handlers.get(name, ())):
            self._call(callback, self)

    def _dispatch(self, name, data, timestamp, source):
        lower = str(name).lower()
        key = next((n for n in self._wanted_names() if n.lower() == lower), None)
        if key is None:
            return
        event = Event(name, data, timestamp, source, self.server_id)
        for callback in list(self._handlers.get(key, ())) + list(self._handlers.get("event", ())):
            self._call(callback, event)

    def _add_handler(self, name, callback) -> str:
        key = canonical(name)
        callbacks = self._handlers.setdefault(key, [])
        if callback not in callbacks:
            callbacks.append(callback)
        return key

    def on(self, name, callback=None):
        if callback is None:
            def decorator(fn):
                self.on(name, fn)
                return fn
            return decorator
        key = self._add_handler(name, callback)
        link = self._link
        if key not in LIFECYCLE and link is not None and link.connected:
            link.subscribe_soon(key)
            if key in JOIN_LEAVE:
                link.ensure_polling()
        return callback

    def off(self, name, callback=None):
        key = canonical(name)
        if callback is None:
            self._handlers.pop(key, None)
            return
        callbacks = self._handlers.get(key, [])
        if callback in callbacks:
            callbacks.remove(callback)

    async def subscribe(self, name):
        key = canonical(name)
        if key in LIFECYCLE:
            raise ValueError(f"{key} is a lifecycle name, not a console event")
        if key not in self._wanted:
            self._wanted.append(key)
        link = self._link
        if link is None or not link.connected:
            return None
        if key in JOIN_LEAVE:
            link.ensure_polling()
        return await link.subscribe(key)

    async def unsubscribe(self, name):
        key = canonical(name)
        self._handlers.pop(key, None)
        if key in self._wanted:
            self._wanted.remove(key)
        link = self._link
        if link is None or not link.connected:
            return None
        return await link.unsubscribe(key)

    def start(self):
        if self._keeper is not None and not self._keeper.done():
            return
        self._closing = False
        self._last_error = None
        self._fatal = None
        if self._done.is_set():
            self._done = asyncio.Event()
        if self._trace is not None:
            try:
                self._trace.open()
            except OSError as e:
                log.warning("Cannot trace %s to %s: %s", self.label, self._trace.target, e)
        self._keeper = asyncio.ensure_future(self._keep())

    async def _keep(self):
        while not self._closing:
            try:
                token = self.token if self.token is not None else read_token(self.token_file)
                link = Link(self.host, self.port, token, label=self.label, wanted=self._wanted_names,
                            emit=self._dispatch, wants_join_leave=self._wants_join_leave, trace=self._trace,
                            **self._link_options)
                await link.connect()
            except Error as e:
                self._attempt_failed(e)
            except Exception as e:
                log.exception("Unexpected error connecting to %s", self.label)
                self._attempt_failed(Error(f"{e.__class__.__name__}: {e}"))
            else:
                if self._closing:
                    await link.aclose()
                    break
                self._last_error = None
                self._fatal = None
                self._link = link
                log.info("Connected to %s", self.label)
                self._settle_waiters(None)
                self._up = True
                self._fire("ready")
                await link.wait_closed()
                if self._link is link:
                    self._link = None
                if self._closing:
                    break
                log.info("Disconnected from %s", self.label)
                if self._up:
                    self._up = False
                    self._fire("close")
            if self._closing:
                break
            await asyncio.sleep(self.retry)

    def _attempt_failed(self, error):
        message = str(error)
        fatal = isinstance(error, (ConsolePermissionsDenied, ConsoleCreateFailedException))
        self._fatal = error if fatal else None
        if fatal and self._waiters:
            self._settle_waiters(error)
        if self._last_error != message:
            self._last_error = message
            log.error("Cannot connect to %s: %s Retrying every %gs.", self.label, message.rstrip(".") + ".",
                      self.retry)
            if isinstance(error, ConsolePermissionsDenied):
                self._fire("rejected")

    def _settle_waiters(self, error):
        for waiter in list(self._waiters):
            if waiter.done():
                continue
            if error is None:
                waiter.set_result(None)
            else:
                waiter.set_exception(error)

    async def _wait_connected(self, timeout):
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.add(waiter)
        try:
            await asyncio.wait_for(asyncio.shield(waiter), timeout)
        except asyncio.TimeoutError:
            self._waiters.discard(waiter)
            raise ConsoleTimeoutException(
                f"no connection to the console at {self.url} after {timeout:g}s") from None
        finally:
            self._waiters.discard(waiter)

    async def connect(self, timeout=None):
        if self.connected:
            return
        self.start()
        try:
            if self._fatal is not None:
                raise self._fatal
            await self._wait_connected(timeout)
        except (ConsoleTimeoutException, ConsolePermissionsDenied, ConsoleCreateFailedException):
            await self.aclose()
            raise
        except asyncio.CancelledError:
            self.close()
            raise

    async def wait_for_ready(self, timeout=None):
        if self.connected:
            return
        if self._fatal is not None:
            raise self._fatal
        await self._wait_connected(timeout)

    async def run(self):
        if self._keeper is None:
            await self.connect()
        await self._done.wait()

    async def command(self, text, timeout=None) -> Reply:
        link = self._link
        if link is None or not link.connected:
            raise ConsoleNotConnectedException(f"not connected to the console at {self.url}")
        command = str(text)
        frame = await link.send(command, timeout or self.command_timeout)
        return Reply.from_frame(command, frame)

    async def players(self):
        reply = (await self.command("player list")).check()
        return reply.roster() or []

    async def message(self, player, text) -> Reply:
        clean = sanitise_console_text(text)
        if clean["empty"]:
            raise ValueError("empty message text")
        return await self.command(f'player message {quoted_player(player)} "{clean["text"]}"')

    def close(self):
        self._closing = True
        link, keeper = self._link, self._keeper
        self._link = None
        self._keeper = None
        if link is not None:
            link.close()
        if self._trace is not None:
            self._trace.close()
        current = asyncio.current_task()
        if keeper is not None and not keeper.done() and keeper is not current:
            keeper.cancel()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        self._settle_waiters(ConsoleClosedException("the console was closed"))
        self._done.set()
        if self._up:
            self._up = False
            self._fire("close")

    async def aclose(self):
        link, keeper = self._link, self._keeper
        self.close()
        if link is not None:
            await link.aclose()
        if keeper is not None and keeper is not asyncio.current_task():
            await asyncio.gather(keeper, return_exceptions=True)
