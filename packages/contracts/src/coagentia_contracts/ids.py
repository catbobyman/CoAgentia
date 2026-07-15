"""基础标量类型（契约 A §1 全局约定的线上形状）。

- Ulid: 26 字符 Crockford Base32 大写（时间有序主键）
- TimestampZ: ISO-8601 UTC 毫秒 `YYYY-MM-DDTHH:MM:SS.sssZ`（可读可排序，与库内零转换）
- Sha256Hex: 64 位小写十六进制（契约 A §2 指纹；短码 = 前 6 位）
"""

from typing import Annotated

from pydantic import StringConstraints

Ulid = Annotated[str, StringConstraints(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]
TimestampZ = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

SHORT_HASH_LEN = 6
