"""确定性内核（03 §3.3）：零第三方依赖、无 IO、无时钟、无随机。

M1 仅指纹（契约 A §2）；V1–V14 校验器与 <control> 解析器随 M6 落地。
"""

from coagentia_contracts.kernel.fingerprint import canonicalize, fingerprint, short_hash

__all__ = ["canonicalize", "fingerprint", "short_hash"]
