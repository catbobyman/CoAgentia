"""cal6 探针 b 对端：stderr 洪泛 2MB，同时 stdout 每 64KB stderr 写一行进度 JSON。"""

import json
import sys

CHUNK = b"E" * 8192
TOTAL_STDERR = 2 * 1024 * 1024  # 2MB
STDOUT_LINES = 32


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    n_chunks = TOTAL_STDERR // len(CHUNK)  # 256
    per_line = n_chunks // STDOUT_LINES  # 每 8 块(64KB stderr)报一行
    for i in range(n_chunks):
        sys.stderr.buffer.write(CHUNK)
        sys.stderr.buffer.flush()
        if (i + 1) % per_line == 0:
            msg = {"line": (i + 1) // per_line, "stderr_bytes": (i + 1) * len(CHUNK)}
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()
    sys.stdout.write(json.dumps({"done": True}) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
