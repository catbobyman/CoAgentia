"""cal7: echo the Authorization header back to the client over WS."""
import asyncio

import websockets


async def handler(ws):
    auth = ws.request.headers.get("Authorization", "<missing>")
    await ws.send(f"auth={auth}")
    await ws.close()


async def main():
    async with websockets.serve(handler, "127.0.0.1", 8921):
        await asyncio.sleep(15)


asyncio.run(main())
