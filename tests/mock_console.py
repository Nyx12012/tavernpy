import json
import re
from datetime import datetime, timezone

from aiohttp import WSMsgType, web

from tavernpy import EVENTS, LOG_EVENTS

ORDER = ["SystemMessage", "Subscription", "CommandResult"]


class MockConsole:
    def __init__(self, token="good.token.sig", enum_as_number=False, refusal="silent", pong=True):
        self.token = token
        self.pong = pong
        self.enum_as_number = enum_as_number
        self.refusal = refusal
        self.drop_prefix = None
        self.players = [{"id": 1001, "username": "Alice"}, {"id": 1002, "username": "Bob Smith"}]
        self.received = []
        self.subscriptions = {}
        self.drop_replies = False
        self.hold = False
        self.held = []
        self.runner = None
        self.port = None

    async def start(self, port=0):
        app = web.Application()
        app.router.add_get("/", self._handler)
        self.runner = web.AppRunner(app, shutdown_timeout=0.1)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", port or self.port or 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        for ws in list(self.subscriptions):
            await ws.close()
        self.subscriptions.clear()
        if self.runner is not None:
            await self.runner.cleanup()
        self.runner = None

    async def drop_all(self):
        for ws in list(self.subscriptions):
            await ws.close()

    def count(self, command):
        return sum(1 for c in self.received if c == command)

    def type(self, name):
        return ORDER.index(name) if self.enum_as_number else name

    def frame(self, kind, event_type, data, command_id=None):
        return json.dumps({"type": self.type(kind), "timeStamp": datetime.now(timezone.utc).isoformat(),
                           "eventType": event_type, "data": data, "commandId": command_id})

    async def _handler(self, request):
        ws = web.WebSocketResponse(autoping=self.pong)
        await ws.prepare(request)
        authed = False
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                if not authed:
                    if msg.data == self.token:
                        authed = True
                        self.subscriptions[ws] = set()
                        await ws.send_str(self.frame("SystemMessage", "InfoLog",
                                                     "Connection Succeeded, Authenticated as: (Server:0)"))
                    elif self.refusal == "invalid":
                        await ws.send_str(self.frame("SystemMessage", "FatalLog", "Invalid Token " + msg.data))
                        await ws.close()
                    continue
                req = json.loads(msg.data)
                if not isinstance(req.get("id"), int):
                    raise ValueError("request id must be a number")
                self.received.append(req["content"])
                if self.drop_replies or (self.drop_prefix and req["content"].startswith(self.drop_prefix)):
                    continue
                if self.hold:
                    self.held.append((ws, req))
                    continue
                await self._answer(ws, req)
        finally:
            self.subscriptions.pop(ws, None)
        return ws

    async def _answer(self, ws, req):
        data = self.handle(ws, req["content"])
        await ws.send_str(self.frame("CommandResult", None, data, req["id"]))

    async def release(self):
        self.hold = False
        held, self.held = self.held, []
        for ws, req in held:
            if not ws.closed:
                await self._answer(ws, req)

    async def push(self, event_type, data):
        sent = 0
        for ws, subs in list(self.subscriptions.items()):
            if event_type in subs:
                await ws.send_str(self.frame("Subscription", event_type, data))
                sent += 1
        return sent

    def subscribed(self, event_type):
        return any(event_type in subs for subs in self.subscriptions.values())

    def outcome(self, name):
        value = ["Success", "Unchanged", "NotFound"].index(name)
        return {"Command": {}, "Result": value if self.enum_as_number else name, "ResultString": name,
                "Exception": None}

    def handle(self, ws, content):
        ok = {"Command": {"Name": "x"}, "ResultString": "Success", "Exception": None}
        args = re.findall(r'"[^"]*"|\S+', content)
        mod = args[0] if args else None
        verb = args[1] if len(args) > 1 else None
        subs = self.subscriptions.setdefault(ws, set())
        if mod == "websocket" and verb == "subscribe":
            name = args[2]
            if name.startswith("Nope"):
                return self.outcome("NotFound")
            if name in subs:
                return self.outcome("Unchanged")
            subs.add(name)
            return self.outcome("Success")
        if mod == "websocket" and verb == "unsubscribe":
            name = args[2]
            if name not in subs:
                return self.outcome("Unchanged")
            subs.discard(name)
            return self.outcome("Success")
        if mod == "websocket" and verb == "subscriptions":
            listed = [{"type": "x", "HasListeners": name in subs, "name": name} for name in LOG_EVENTS + EVENTS]
            return {"Command": {}, "Result": listed, "Exception": None,
                    "ResultString": "System.Collections.Generic.Dictionary`2+ValueCollection[System.String,Alta.Console.IConsoleEvent]"}
        if mod == "player" and verb == "list":
            return {"Command": {}, "Result": [dict(p) for p in self.players],
                    "ResultString": "System.Collections.Generic.List`1[System.Object]",
                    "Exception": None}
        if mod == "player" and verb == "message":
            return ok
        return {"Command": None, "ResultString": None,
                "Exception": {"ClassName": "System.Exception", "Message": "[CommandService.Handle] Command not found"}}
