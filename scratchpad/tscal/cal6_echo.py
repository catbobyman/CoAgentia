"""cal6 探针 a 对端：JSON 行回声（stdin 收一行 JSON -> stdout 回一行 JSON，UTF-8，逐行 flush）。"""

import json
import sys
import time


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        obj["echo_ts"] = time.time()
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
