import asyncio
import os
import sys

from tavernpy import Console

sys.stdout.reconfigure(encoding="utf-8")

console = Console(os.environ.get("TAVERN_HOST", "127.0.0.1"), int(os.environ.get("TAVERN_PORT", "1760")),
                  token_file=os.environ.get("TAVERN_TOKEN_FILE"))
WELCOME_DELAY = float(os.environ.get("TAVERN_WELCOME_DELAY", "30"))


@console.on("PlayerJoined")
async def welcome(event):
    name = event.data["user"]["username"]
    print(name, "joined", flush=True)
    await asyncio.sleep(WELCOME_DELAY)
    reply = await console.message(name, "Welcome to the server!")
    print("Welcomed", name, "ok" if reply.ok else reply.error, flush=True)


async def main():
    async with console:
        names = [p.username for p in await console.players()]
        print("Online:", ", ".join(names) or "nobody", flush=True)
        await console.run()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
