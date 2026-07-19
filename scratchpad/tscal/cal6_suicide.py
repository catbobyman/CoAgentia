"""cal6 探针 d 对端：写 32KB 残留数据 + 终言行 + 一行 stderr，flush 后 os._exit(42) 自杀。

32KB < win32 管道缓冲(64KB)，保证进程能把数据全部塞进管道后立即死亡，
从而测试 node 侧在子进程已死后能否取回管道内残留数据。
"""

import os
import sys

PAYLOAD = ("X" * 1023 + "\n") * 32  # 32768 字节


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.write("HELLO\n")
    sys.stdout.flush()
    sys.stdout.write(PAYLOAD)
    sys.stdout.write("LAST-WORDS marker=cal6-final\n")
    sys.stdout.flush()
    sys.stderr.write("dying-now code=42\n")
    sys.stderr.flush()
    os._exit(42)


if __name__ == "__main__":
    main()
