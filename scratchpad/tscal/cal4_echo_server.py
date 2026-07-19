"""cal4 探针对端: websockets echo server, 端口 8917.

- 普通消息(文本/二进制)原样回显
- "__ping__": 服务端发协议级 ping, 等 pong, 回 "PONG_OK:<ms>"
- "__close__": 服务端主动 close(code=4001, reason="server-bye")
"""

import asyncio
import time

import websockets


async def handler(ws, path=None):  # 兼容新旧 websockets handler 签名
    async for msg in ws:
        if isinstance(msg, str) and msg == "__ping__":
            t0 = time.perf_counter()
            pong_waiter = await ws.ping()
            await pong_waiter
            dt = (time.perf_counter() - t0) * 1000
            await ws.send(f"PONG_OK:{dt:.2f}")
        elif isinstance(msg, str) and msg == "__close__":
            await ws.close(code=4001, reason="server-bye")
        else:
            await ws.send(msg)


async def main():
    async with websockets.serve(
        handler, "127.0.0.1", 8917, max_size=16 * 1024 * 1024
    ):
        print("READY", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
