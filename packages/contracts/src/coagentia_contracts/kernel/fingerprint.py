"""规范化序列化与指纹（契约 A §2 的 Python 权威实现）。

规范条文（逐条对应 A §2.1–2.4）：
1. 值域仅 JSON 子集：object/array/string/整数/true/false/null；禁 float/NaN/Infinity。
2. null 剔除：递归删除对象中值为 null 的键（缺席 ≡ null）；数组内禁止 null。
3. 序列化：UTF-8；对象键按 Unicode 码点升序；分隔符后无空白；非 ASCII 不转义。
4. SHA-256 → 64 位小写 hex；短码 = 前 6 位。

依赖纪律：仅标准库（00 §3 澄清 2）。TS 镜像实现以 packages/fixtures/golden/ 判例集为唯一验收标准。
"""

import hashlib
import json

from coagentia_contracts.ids import SHORT_HASH_LEN

type JsonSubset = dict[str, "JsonSubset"] | list["JsonSubset"] | str | int | bool | None


def _prune(value: JsonSubset, *, in_array: bool = False) -> JsonSubset:
    """校验值域并执行 null 剔除；违规抛 ValueError（fail-closed，不静默容忍）。"""
    if value is None:
        if in_array:
            raise ValueError("null is forbidden inside arrays (contract A section 2.2)")
        return None
    if isinstance(value, bool):  # bool 先于 int 判断（Python bool 是 int 子类）
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ValueError("float is forbidden in fingerprinted content (contract A section 2.1)")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_prune(v, in_array=True) for v in value]
    if isinstance(value, dict):
        out: dict[str, JsonSubset] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError("object keys must be strings")
            if v is None:
                continue  # null 剔除（缺席 ≡ null）
            out[k] = _prune(v)
        return out
    raise ValueError(f"unsupported type in fingerprinted content: {type(value).__name__}")


def canonicalize(value: JsonSubset) -> str:
    """规范化序列化：键按码点升序、无空白、非 ASCII 原样。"""
    return json.dumps(_prune(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value: JsonSubset) -> str:
    """SHA-256(canonicalize(value)) → 64 位小写十六进制。"""
    return hashlib.sha256(canonicalize(value).encode("utf-8")).hexdigest()


def short_hash(full_hash: str) -> str:
    """UI 展示短码（设计稿样例 `a1b2c3` 即此）。"""
    return full_hash[:SHORT_HASH_LEN]
