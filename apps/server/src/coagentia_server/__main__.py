"""入口：`coagentia-server` 启动 FastAPI（uvicorn，带 websockets——契约 C WS 面）。"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run(
        "coagentia_server.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8787,
    )


if __name__ == "__main__":
    main()
