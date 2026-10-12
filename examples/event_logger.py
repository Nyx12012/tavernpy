import asyncio
import json
import os
import sys

from tavernpy import CHATTY, EVENTS, LOG_EVENTS, Console

QUIET = set(CHATTY) | {"TraceLog", "DebugLog"}


async def main(out):
    console = Console(os.environ.get("TAVERN_HOST", "127.0.0.1"), int(os.environ.get("TAVERN_PORT", "1760")),
                      token_file=os.environ.get("TAVERN_TOKEN_FILE"))

    @console.on("event")
    def write(event):
        line = {"time": event.timestamp, "type": event.type, "source": event.source, "data": event.data}
        out.write(json.dumps(line) + "\n")
        out.flush()

    def lost(c):
        if not c.closed:
            print("lost", c.label, "and reconnecting", file=sys.stderr, flush=True)

    console.on("ready", lambda c: print("connected to", c.label, file=sys.stderr, flush=True))
    console.on("close", lost)
    for name in EVENTS + LOG_EVENTS:
        if name not in QUIET:
            await console.subscribe(name)
    async with console:
        await console.run()


out = open(sys.argv[1], "a", encoding="utf-8") if len(sys.argv) > 1 else sys.stdout
try:
    asyncio.run(main(out))
except KeyboardInterrupt:
    pass
