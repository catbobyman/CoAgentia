"""daemon 侧 ULID / 时间戳生成（与 server ledger.new_ulid / now_iso 同算法，两端字典序一致）。

契约 A §1：26 字符 Crockford Base32 大写 ULID（48-bit 毫秒 + 80-bit 随机；天然排除 I/L/O/U）；
时间戳 ISO-8601 UTC 毫秒 Z。daemon 不 import server，故此处独立实现同一算法。
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford Base32（排除 I/L/O/U）


def new_ulid() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def now_iso() -> str:
    dt = datetime.now(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
