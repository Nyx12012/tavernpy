# tavernpy

An async Python client for the console of a self-hosted A Township Tale server (TavernLib, port
1760). Connect, send commands, and get game events, with reconnects handled for you.

It picks up where [py-tale](https://github.com/dly2424/py-tale) left off. py-tale talked to Alta's
cloud, which is gone; tavernpy talks straight to your server.

## Install

```
pip install tavernpy
```

or straight from GitHub:

```
pip install git+https://github.com/Nyx12012/tavernpy
```

Python 3.11 or newer. The only dependency is aiohttp.

## A small bot

```python
import asyncio
from tavernpy import Console

console = Console("127.0.0.1", token_file="console_token.txt")


@console.on("PlayerJoined")
async def welcome(event):
    await asyncio.sleep(30)
    await console.message(event.data["user"]["username"], "Welcome to the server!")


async def main():
    async with console:
        print(await console.players())
        await console.run()

asyncio.run(main())
```

`PlayerJoined` comes when the player's client connects. A message sent in the first 15 seconds or so
is not shown in game (one sent 25 seconds after a join was), so the bot waits 30 seconds first.

[examples/bot.py](https://github.com/Nyx12012/tavernpy/blob/main/examples/bot.py) is this bot (`TAVERN_WELCOME_DELAY` sets the wait), and
[examples/event_logger.py](https://github.com/Nyx12012/tavernpy/blob/main/examples/event_logger.py) writes every event as a JSON line. Both read
`TAVERN_HOST`, `TAVERN_PORT` and `TAVERN_TOKEN_FILE`.

## The token

The console token is the whole of `console_token.txt` in the server's `TheModdingTavern` folder
(on Windows `%APPDATA%\TheModdingTavern\console_token.txt`, which is the default when you pass
neither `token` nor `token_file`). The server writes it on first start.

That token never expires and can run every console command, and the console is plain `ws://`.
Keep port 1760 off the internet: connect on the same machine, over a VPN, or through an SSH tunnel.

## Console

`Console(host="127.0.0.1", port=1760, *, token=None, token_file=None, server_id=None, trace=None)`

| | |
|---|---|
| `await connect(timeout=None)` | connect and wait. Raises `ConsolePermissionsDenied` for a refused token, `ConsoleCreateFailedException` for a missing token file, `ConsoleTimeoutException` when `timeout` runs out. A server that is down is retried every 10 s until it answers |
| `start()` | connect in the background and never raise |
| `await command(text)` | send a command, get a `Reply` |
| `on(name, callback)` | handle an event, also usable as `@console.on(name)`. Callbacks may be plain or async |
| `off(name, callback=None)` | remove a handler |
| `await subscribe(name)` / `await unsubscribe(name)` | ask the server for an event without a handler (pair it with `on("event")`), or stop it. Both return the server's answer: `"Success"`, `"Unchanged"` or `"NotFound"` |
| `await players()` | the online players as `Player(username, id)` |
| `await message(player, text)` | show text to a player in game |
| `await run()` | wait until `close()` |
| `close()` / `await aclose()` | disconnect for good |
| `connected`, `closed`, `subscriptions`, `handlers` | state |

More keywords tune the timing: `retry` (10 s between connection attempts), `command_timeout` (30 s),
`ping_interval` (30 s; a connection that misses a ping for that long is dropped and made again) and
`poll_join_leave` (see Events).

Besides game events, `on` takes four names of its own: `"ready"` (connected, again after every
reconnect), `"close"` (disconnected; `console.closed` tells a lost connection from your own
`close()`), `"rejected"` (token refused) and `"event"` (every event). The first three call back with
the console.

Subscriptions survive reconnects. After each reconnect the client sends them again, and a few more
times over the next 40 seconds.

A server that has just started takes commands at once, then stops running them while it loads the
world (one to four minutes on our test server), and runs every queued command afterwards. A command
sent in that time times out, but the server may still run it later, so don't blindly send one again
if it changes something. tavernpy waits for the late answers to its own subscribes instead of
sending them again (it sends one again only after ten minutes without an answer), and logs one line
when the server goes quiet and one when it answers again. Until the world has loaded, some commands, like
`player list`, answer `Command not found`.

A wrong token gets no answer from the server at all, so tavernpy reads 10 seconds of silence after
the token as a refusal.

## Replies

`Reply` keeps the three parts of a console answer apart:

- `result`: the structured value, if the command returned one
- `result_string`: the text line. A bare .NET type name next to a real result is blanked
- `error`: set when the server refused the command

`ok` is `not error`, and `check()` raises `CommandFailedException` when the command failed.
`value` gives `result` if it has one, otherwise `result_string`. `text` turns it into a string,
`json()` parses JSON text, and `raw` holds the frame exactly as received.

## Events

`EVENTS` lists 17 game events and `LOG_EVENTS` the 6 log streams, the same 23 that
`websocket subscriptions` lists on a stock server. Names match in any case, and names outside the
list are passed through, so events added by server mods work too. `CHATTY` names the busy ones:
`InventoryChanged`, `PlayerMovedChunk` and `ProfilingData`.

Each callback gets an `Event` with `type`, `data`, `timestamp`, `source` and `server_id`. A
`PlayerJoined` looks like `{"user": {"id": 1000000000, "username": "Name"}, "mode": "OpenVR",
"position": [...]}`.

A stock TavernLib server sends `PlayerJoined` itself. For a server that doesn't, pass
`poll_join_leave=True`: while something listens for joins or leaves, tavernpy then polls
`player list` every 10 seconds and sends them itself with `source` set to `"poll"`, about 20
seconds after the change. The first real one from the server stops the polling.

## Many servers

`Tavern` holds one `Console` per server, using py-tale's method names:

```python
tavern = Tavern()
await tavern.create_console("home", "127.0.0.1", token_file="home_token.txt", ensure_open=True)
await tavern.console_sub("PlayerKilled", on_killed)
reply = await tavern.send_command_console("home", "player list")
```

`create_console`, `send_command_console`, `console_sub`, `console_unsub`, `get_active_consoles`,
`get_console_subs`, `wait_for_ready`, `run`, plus `get_console`, `remove_console` and `aclose`.
A `console_sub` without `server_id` covers every console, including ones created later.
`console_sub` and `console_unsub` return each server's answer, like `{"home": "Success"}`.
Keywords given to `Tavern()` go to every `Console` it creates.

## Seeing the wire

Pass `trace="frames.txt"`, or set `TAVERN_TRACE=frames.txt`, and every frame in and out is appended
to that file with a timestamp, the console's name and a direction (`>` sent, `<` received, `*` the
connection itself). The token is written as `***`. `trace=False` turns it off even when
`TAVERN_TRACE` is set.

```
2026-10-11T12:35:19.770 127.0.0.1:1760 > {"id": 2, "content": "player list"}
2026-10-11T12:35:19.771 127.0.0.1:1760 < {"type": "CommandResult", "commandId": 2, ...}
```

## Coming from py-tale

Logins, groups, invites, bans and the cloud websocket are gone, along with every `request_*`
function and `config`. You give each server its host, port and token instead. `send_command_console`
returns a `Reply` rather than the raw frame, and callbacks get an `Event` rather than a dict.

## Errors

Everything raised is a `tavernpy.Error`: `ConsolePermissionsDenied`, `ConsoleTimeoutException`,
`ConsoleCreateFailedException`, `ConsoleAlreadyCreatedException`, `CommandTimeoutException`,
`ConsoleNotConnectedException`, `ConsoleClosedException`, `CommandFailedException` and
`BadPlayerNameException` (also a `ValueError`). Player names must be letters, digits, spaces,
`_`, `.` or `-`, at most 32 characters.

## Tested

Against a stock TavernLib 1.5.2 server: logging in and a refused token, commands and their replies,
`players()`, `message()` seen in game, the events the server lists, joins and leaves, reconnecting
across server restarts, `unsubscribe`, `Tavern`, and a clean close.

Tested offline only: the join and leave polling (stock servers send joins themselves) and player
names with spaces.

## Tests

```
pip install -e .[dev]
pytest
```

The tests run against a mock console written in Python. A few more need a fake console written in
Node that is not part of this repository; they skip without it.

## License

MIT, see [LICENSE](https://github.com/Nyx12012/tavernpy/blob/main/LICENSE).
