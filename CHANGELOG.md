# Changelog

## 1.0.0 (2026-10-12)

First public release.

- `Console` talks to one server's console. It reconnects on its own, sends its subscriptions again
  after every reconnect, and drops a connection that stops answering pings.
- `Tavern` holds one `Console` per server and keeps py-tale's method names.
- `Reply` keeps a command's result, result text and error apart. `check()` raises when the command
  failed.
- Events: the 23 a stock TavernLib server lists, and any event a server mod adds.
- `players()` reads the online players and `message()` shows text to one of them.
- A server that has just started stops running commands for a minute or more while it loads the
  world. tavernpy matches the late answers to its subscribes instead of sending them again, and
  logs the stall in two lines.
- `poll_join_leave=True` makes joins and leaves from `player list` for a server that doesn't send them.
- `Console(trace=...)` or `TAVERN_TRACE` writes every frame in and out to a file, with the token masked.
- Two examples: a bot that welcomes players, and an event logger.
