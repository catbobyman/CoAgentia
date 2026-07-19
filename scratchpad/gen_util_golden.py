"""TS 迁移批 W0：ULID/时间戳跨语言 golden 生成器（任务书裁决 #8）。

对 py 权威实现（coagentia_daemon.util.new_ulid / now_iso）注入固定 time/urandom，
产出 apps/daemon-ts/tests/fixtures/util_golden.json；TS 侧 util.test.ts 逐字节守门。
可复跑：uv run python scratchpad/gen_util_golden.py（输出确定性）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from coagentia_daemon import util

CASES = [
    # (timestamp_ms, random_10_bytes_hex)
    (0, "00000000000000000000"),
    (1, "00000000000000000001"),
    (1721385600123, "0123456789abcdef0123"),
    (1784465643822, "ffffffffffffffffffff"),
    (281474976710655, "deadbeefcafebabe1234"),  # 48-bit 毫秒上限
]

TS_CASES = [0, 1, 1721385600123, 1784465643822, 1784500000999]


def main() -> None:
    ulid_cases = []
    for ms, rand_hex in CASES:
        rand = bytes.fromhex(rand_hex)
        with (
            mock.patch.object(util.time, "time", return_value=ms / 1000.0),
            mock.patch.object(util.os, "urandom", return_value=rand),
        ):
            # time.time()*1000 经浮点可能损失精度——与 py 实现完全同路径取整
            expected = util.new_ulid()
        ulid_cases.append({"timestamp_ms": ms, "random_hex": rand_hex, "expected": expected})

    iso_cases = []
    for ms in TS_CASES:
        dt = datetime.fromtimestamp(ms / 1000.0, UTC)
        with mock.patch.object(util, "datetime", wraps=util.datetime) as dt_mod:
            dt_mod.now.return_value = dt
            expected = util.now_iso()
        iso_cases.append({"epoch_ms": ms, "expected": expected})

    out = Path(__file__).parents[1] / "apps" / "daemon-ts" / "tests" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    (out / "util_golden.json").write_text(
        json.dumps({"ulid": ulid_cases, "now_iso": iso_cases}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"golden -> {out / 'util_golden.json'}: {len(ulid_cases)} ulid + {len(iso_cases)} iso")


if __name__ == "__main__":
    main()
