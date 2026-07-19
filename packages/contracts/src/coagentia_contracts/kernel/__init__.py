"""确定性内核（03 §3.3）：零第三方依赖、无 IO、无时钟、无随机。

M1 指纹（契约 A §2）。DEDAG 批（2026-07-18）：graph/decomposition 两组随 DAG 编排退役,
fingerprint 组保留（freshness/幂等身份全仓消费）。
"""

from coagentia_contracts.kernel.fingerprint import canonicalize, fingerprint, short_hash

__all__ = [
    "canonicalize",
    "fingerprint",
    "short_hash",
]
