import asyncio
import json
import logging
import os
import re
import time

import aiohttp

from .errors import (CommandTimeoutException, ConsoleClosedException, ConsoleCreateFailedException,
                     ConsoleNotConnectedException, ConsolePermissionsDenied, Error)
from .events import JOIN_LEAVE
from .reply import response_type, roster_from_result, subscription_outcome, to_command_result

log = logging.getLogger("tavernpy")

POLL_GRACE = 20.0
POLL_INTERVAL = 10.0
AUTH_TIMEOUT = 10.0
COMMAND_TIMEOUT = 30.0
PING_INTERVAL = 30.0
PRIME_TIMEOUT = 8.0
RETRY_SUBSCRIBE = 15.0
RESEND_UNANSWERED = 600.0
LATE_MEMORY = 1000
SETTLE_DELAYS = (8.0, 20.0, 40.0)
MAX_REQUEST_ID = 2147483647


def default_token_file():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(appdata, "TheModdingTavern", "console_token.txt")


def read_token(token_file) -> str:
    if not token_file:
        raise ConsoleCreateFailedException("No console token. Pass token= or token_file=.")
    try:
        with open(token_file, "rb") as f:
            raw = f.read()
    except OSError:
        raise ConsoleCreateFailedException(
            f"Cannot read the console token at {token_file}. Start the game server once so it writes "
            "the file, or copy console_token.txt from the server."
        ) from None
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    token = text.strip()
    if not token:
        raise ConsoleCreateFailedException(f"The console token file {token_file} is empty.")
    return token


def _fmt_seconds(value: float) -> str:
    return f"{value:g}s"


class Link:
    def __init__(self, host, port, token, *, label=None, wanted=None, emit=None, wants_join_leave=None,
                 poll_interval=POLL_INTERVAL, poll_join_leave=False, command_timeout=COMMAND_TIMEOUT,
                 auth_timeout=AUTH_TIMEOUT, ping_interval=PING_INTERVAL, settle_delays=SETTLE_DELAYS,
                 poll_grace=POLL_GRACE, prime_timeout=PRIME_TIMEOUT, retry_subscribe=RETRY_SUBSCRIBE,
                 resend_unanswered=RESEND_UNANSWERED, trace=None):
        self.host = host
        self.port = int(port)
        self.token = token
        self.label = label or f"{host}:{port}"
        self._wanted = wanted or (lambda: ())
        self._emit = emit or (lambda name, data, timestamp, source: None)
        self._wants_join_leave = wants_join_leave or (lambda: False)
        self.poll_interval = poll_interval
        self.poll_join_leave = poll_join_leave
        self.command_timeout = command_timeout
        self.auth_timeout = auth_timeout
        self.ping_interval = ping_interval
        self.settle_delays = settle_delays
        self.poll_grace = poll_grace
        self.prime_timeout = prime_timeout
        self.retry_subscribe = retry_subscribe
        self.resend_unanswered = resend_unanswered
        self._trace = trace
        self._token_sent = False
        self._inflight = set()
        self._unanswered = {}
        self._late = {}
        self._stall_since = None
        self._not_found = set()
        self._session = None
        self._ws = None
        self._closed = False
        self._closed_event = asyncio.Event()
        self._abort_task = None
        self._next_id = 1
        self._pending = {}
        self._server_subs = set()
        self._awaiting_pong = False
        self._tasks = set()
        self._timers = set()
        self._known = None
        self._polling = False
        self._feed_live = False
        self._feed_seen = {}
        self._held_polls = {}

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def connected(self) -> bool:
        return not self._closed and self._ws is not None and not self._ws.closed

    @property
    def feed_live(self) -> bool:
        return self._feed_live

    async def wait_closed(self):
        await self._closed_event.wait()
        await self._finish()

    def _note(self, direction, text):
        if self._trace is not None:
            self._trace.write(direction, text, self.token)

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.warning("%s: %s", self.label, task.exception())

    def _later(self, delay, fn, *args):
        handle = None

        def fire():
            self._timers.discard(handle)
            fn(*args)

        handle = asyncio.get_running_loop().call_later(delay, fire)
        self._timers.add(handle)
        return handle

    async def connect(self):
        self._session = aiohttp.ClientSession()
        try:
            await asyncio.wait_for(self._login(self.token), self.auth_timeout)
        except asyncio.TimeoutError:
            refused = self._token_sent and self._ws is not None and not self._ws.closed
            await self._abort()
            if refused:
                raise ConsolePermissionsDenied(
                    f"the console did not accept the token within {_fmt_seconds(self.auth_timeout)}. A wrong "
                    "token gets no answer: copy a fresh console_token.txt."
                ) from None
            raise Error(f"no login reply from the console at {self.url}") from None
        except Error:
            await self._abort()
            raise
        except (aiohttp.ClientError, OSError) as e:
            await self._abort()
            raise Error(f"cannot reach the console at {self.url} ({e.__class__.__name__}: {e})") from None
        except asyncio.CancelledError:
            await asyncio.shield(self._abort())
            raise
        self._spawn(self._reader())
        self._spawn(self._pinger())
        try:
            try:
                await self.send("websocket subscriptions", self.prime_timeout)
            except Error as e:
                log.warning("%s: priming round trip failed: %s", self.label, e)
            if not self.connected:
                raise Error(f"the console at {self.url} closed the connection during login")
            self._resubscribe_all()
            for delay in self.settle_delays:
                self._later(delay, self._resubscribe_all)
            if self.retry_subscribe:
                self._later(self.retry_subscribe, self._retry_missing)
            self.ensure_polling()
        except BaseException:
            self.close()
            await self._finish()
            raise

    async def _login(self, token):
        ws = await self._session.ws_connect(self.url, autoping=False, compress=0, max_msg_size=64 * 1024 * 1024)
        self._ws = ws
        self._note("*", f"open {self.url}")
        self._note(">", token)
        await ws.send_str(token)
        self._token_sent = True
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.PING:
                self._note("*", "ping from server")
                await ws.pong(msg.data)
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                self._note("*", msg.type.name.lower())
                continue
            self._note("<", msg.data)
            try:
                frame = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            kind = response_type(frame.get("type"))
            data = frame.get("data")
            message = data if isinstance(data, str) else json.dumps(data)
            if kind == "SystemMessage" and re.search("Connection Succeeded", message, re.I):
                return
            if kind == "SystemMessage" and re.search("Invalid Token|refused", message, re.I):
                raise ConsolePermissionsDenied(
                    "the console rejected the token. Copy a fresh console_token.txt "
                    "(it changes if server_secret.key is deleted)."
                )
        raise Error(f"the console at {self.url} closed the connection during login")

    async def _abort(self):
        ws, session = self._ws, self._session
        self._ws = None
        self._session = None
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), 2)
            except Exception:
                pass
        if session is not None:
            await session.close()

    async def _reader(self):
        ws = self._ws
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._note("<", msg.data)
                    try:
                        frame = json.loads(msg.data)
                    except ValueError:
                        continue
                    if isinstance(frame, dict):
                        self._handle_frame(frame)
                elif msg.type == aiohttp.WSMsgType.PING:
                    self._note("*", "ping from server")
                    await ws.pong(msg.data)
                elif msg.type == aiohttp.WSMsgType.PONG:
                    self._note("*", "pong")
                    self._awaiting_pong = False
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    self._note("*", f"{msg.type.name.lower()} {ws.close_code}")
                    break
        except (aiohttp.ClientError, OSError, asyncio.CancelledError):
            pass
        finally:
            self.close()

    async def _pinger(self):
        while not self._closed:
            await asyncio.sleep(self.ping_interval)
            if self._closed or self._ws is None:
                return
            if self._awaiting_pong:
                log.warning("No answer from %s for %s, reconnecting.", self.label, _fmt_seconds(self.ping_interval))
                self._note("*", "no pong, closing")
                self.close()
                return
            self._awaiting_pong = True
            self._note("*", "ping")
            try:
                await self._ws.ping()
            except Exception:
                pass

    def _handle_frame(self, frame):
        kind = response_type(frame.get("type"))
        if kind == "CommandResult":
            cid = frame.get("commandId")
            try:
                key = int(cid)
            except (TypeError, ValueError):
                return
            fut = self._pending.pop(key, None)
            if fut is not None:
                if not fut.done():
                    fut.set_result(frame)
            elif key in self._late:
                on_late = self._late.pop(key)
                if on_late is not None:
                    on_late(frame)
            self._answering()
            return
        if kind == "Subscription":
            name = frame.get("eventType")
            if not name:
                return
            if name in JOIN_LEAVE:
                self._feed_reported_join_leave(name, frame.get("data"))
                if not self._feed_live:
                    self._feed_live = True
                    self._known = None
            self._emit(name, frame.get("data"), frame.get("timeStamp"), "feed")

    async def send(self, command: str, timeout=None, on_late=None) -> dict:
        if not self.connected:
            raise ConsoleNotConnectedException("not connected to the game console")
        cid = self._next_id
        self._next_id = 1 if self._next_id >= MAX_REQUEST_ID else self._next_id + 1
        wait = timeout or self.command_timeout
        sent = time.monotonic()
        fut = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        payload = json.dumps({"id": cid, "content": command})
        self._note(">", payload)
        try:
            await self._ws.send_str(payload)
        except Exception as e:
            self._pending.pop(cid, None)
            if fut.done() and not fut.cancelled():
                fut.exception()
            raise ConsoleClosedException(f"could not send to the game console: {e}") from None
        try:
            return await asyncio.wait_for(asyncio.shield(fut), wait)
        except asyncio.TimeoutError:
            self._timed_out(cid, sent, wait, on_late)
            raise CommandTimeoutException(
                f"console command timed out after {_fmt_seconds(wait)}: {command}. The server may still run it later."
            ) from None
        finally:
            self._pending.pop(cid, None)

    def _timed_out(self, cid, sent, wait, on_late):
        self._late[cid] = on_late
        while len(self._late) > LATE_MEMORY:
            del self._late[next(iter(self._late))]
        if self._stall_since is None:
            self._stall_since = sent
            log.warning("No answer from %s in %s. A server that just started stops running commands for a minute or "
                        "more while it loads the world, then runs them all.", self.label, _fmt_seconds(wait))

    def _answering(self):
        if self._stall_since is not None:
            waited = _fmt_seconds(round(time.monotonic() - self._stall_since))
            self._stall_since = None
            log.info("%s is answering commands again, after %s.", self.label, waited)

    async def subscribe(self, event_name) -> str:
        self._server_subs.add(event_name)
        self._inflight.add(event_name)
        try:
            res = await self.send(f"websocket subscribe {event_name}",
                                  on_late=lambda frame: self._answered_late(event_name, frame))
        except CommandTimeoutException:
            self._unanswered[event_name] = time.monotonic()
            raise
        except Error:
            self._server_subs.discard(event_name)
            raise
        finally:
            self._inflight.discard(event_name)
        self._unanswered.pop(event_name, None)
        return self._subscribed(event_name, res)

    def _answered_late(self, event_name, frame):
        self._unanswered.pop(event_name, None)
        self._subscribed(event_name, frame)

    def _subscribed(self, event_name, res) -> str:
        outcome = subscription_outcome(to_command_result(res))
        if outcome == "NotFound":
            self._not_found.add(event_name)
        elif outcome in ("Success", "Unchanged"):
            self._not_found.discard(event_name)
        if outcome not in ("Success", "Unchanged"):
            log.warning("Subscribing to %s on %s: %s", event_name, self.label, outcome)
            if outcome == "NotFound" or outcome.startswith("error:"):
                self._server_subs.discard(event_name)
        return outcome

    async def unsubscribe(self, event_name) -> str:
        self._server_subs.discard(event_name)
        self._unanswered.pop(event_name, None)
        res = await self.send(f"websocket unsubscribe {event_name}")
        return subscription_outcome(to_command_result(res))

    def subscribe_soon(self, event_name, force=False):
        if not self.connected or event_name in self._inflight:
            return
        waiting = self._unanswered.get(event_name)
        if waiting is not None:
            if time.monotonic() - waiting < self.resend_unanswered:
                return
        elif event_name in self._server_subs and not force:
            return
        self._server_subs.add(event_name)
        self._spawn(self._send_subscribe(event_name))

    async def _send_subscribe(self, event_name):
        try:
            await self.subscribe(event_name)
        except CommandTimeoutException:
            pass
        except Error as e:
            if not self._closed:
                log.warning("Could not subscribe to %s on %s: %s", event_name, self.label, e)

    def _resubscribe_all(self):
        if not self.connected:
            return
        for name in list(self._wanted()):
            self.subscribe_soon(name, force=True)

    def _retry_missing(self):
        if not self.connected:
            return
        for name in list(self._wanted()):
            if name not in self._not_found:
                self.subscribe_soon(name)
        self._later(self.retry_subscribe, self._retry_missing)

    @staticmethod
    def _join_leave_key(event_name, username) -> str:
        return event_name + " " + ("" if username is None else str(username)).strip().lower()

    def _feed_reported_join_leave(self, event_name, data):
        user = data.get("user") if isinstance(data, dict) else None
        username = user.get("username") if isinstance(user, dict) else None
        if not username:
            return
        key = self._join_leave_key(event_name, username)
        held = self._held_polls.pop(key, None)
        if held is not None:
            held.cancel()
            self._timers.discard(held)
        now = time.monotonic()
        self._feed_seen[key] = now
        for k, ts in list(self._feed_seen.items()):
            if now - ts > self.poll_grace:
                del self._feed_seen[k]

    def _emit_polled_join_leave(self, event_name, player):
        key = self._join_leave_key(event_name, player["username"])
        seen = self._feed_seen.get(key)
        if seen is not None and time.monotonic() - seen <= self.poll_grace:
            return
        if key in self._held_polls:
            return

        def fire():
            self._held_polls.pop(key, None)
            if self._closed:
                return
            user = {"username": player["username"]}
            if player.get("id") is not None:
                user["id"] = player["id"]
            self._emit(event_name, {"user": user}, None, "poll")

        self._held_polls[key] = self._later(self.poll_grace, fire)

    def ensure_polling(self):
        if (self._closed or not self.poll_join_leave or self._feed_live or self._polling
                or not self._wants_join_leave()):
            return
        self._polling = True
        self._spawn(self._poll_loop())

    async def _poll_loop(self):
        try:
            while not self._closed and not self._feed_live and self._wants_join_leave():
                try:
                    roster = roster_from_result(to_command_result(await self.send("player list")))
                except Error:
                    roster = None
                if roster is not None and not self._feed_live and not self._closed:
                    now = {p["username"]: p for p in roster}
                    if self._known is None:
                        self._known = now
                    else:
                        for name, p in now.items():
                            if name not in self._known:
                                self._emit_polled_join_leave("PlayerJoined", p)
                        for name, p in self._known.items():
                            if name not in now:
                                self._emit_polled_join_leave("PlayerLeft", p)
                        self._known = now
                await asyncio.sleep(self.poll_interval)
        finally:
            self._polling = False
            self._known = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._note("*", "closed")
        self._known = None
        for handle in list(self._timers):
            handle.cancel()
        self._timers.clear()
        self._held_polls.clear()
        self._feed_seen.clear()
        self._late.clear()
        self._unanswered.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConsoleClosedException("the game console connection closed"))
        self._pending.clear()
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        try:
            self._abort_task = asyncio.get_running_loop().create_task(self._abort())
        except RuntimeError:
            pass
        self._closed_event.set()

    async def _finish(self):
        if self._abort_task is not None:
            await asyncio.shield(self._abort_task)
        else:
            await self._abort()

    async def aclose(self):
        self.close()
        await self._finish()
