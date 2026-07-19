"""cal6 探针 c 对端：stdin 二进制汇（可选 --slow 慢读制造背压），EOF 后 stdout 报 bytes+sha256。"""

import hashlib
import json
import sys
import time


def main() -> None:
    slow = "--slow" in sys.argv
    h = hashlib.sha256()
    total = 0
    stdin = sys.stdin.buffer
    while True:
        chunk = stdin.read(65536)
        if not chunk:
            break
        h.update(chunk)
        total += len(chunk)
        if slow:
            time.sleep(0.002)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(json.dumps({"bytes": total, "sha256": h.hexdigest()}) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
