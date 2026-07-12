"""确定性内核（03 §3.3）：零第三方依赖、无 IO、无时钟、无随机。

M1 指纹（契约 A §2）；M3b 图内核（detect_cycle/derive_blocked）；
M6b 拆解校验 parse_control/validate_proposal/proposal_fingerprint（契约 B §12.2 / 拆解设计 §5–6）。
"""

from coagentia_contracts.kernel.decomposition import (
    parse_control,
    proposal_fingerprint,
    validate_proposal,
)
from coagentia_contracts.kernel.fingerprint import canonicalize, fingerprint, short_hash

__all__ = [
    "canonicalize",
    "fingerprint",
    "parse_control",
    "proposal_fingerprint",
    "short_hash",
    "validate_proposal",
]
