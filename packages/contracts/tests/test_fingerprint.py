"""指纹规范测试（契约 A §2 逐条）+ golden 判例（跨语言验收标准）。"""

import json
from pathlib import Path

import pytest
from coagentia_contracts.kernel import canonicalize, fingerprint, short_hash

GOLDEN = Path(__file__).parents[2] / "fixtures" / "golden" / "fingerprint.json"


def test_key_sorting_by_codepoint() -> None:
    assert canonicalize({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    # 'Z'(0x5A) < 'a'(0x61)：按码点不是按字典序
    assert canonicalize({"a": 2, "Z": 1}) == '{"Z":1,"a":2}'


def test_null_pruned_from_objects_recursively() -> None:
    assert canonicalize({"a": None, "b": {"c": None, "d": 1}}) == '{"b":{"d":1}}'


def test_unicode_not_escaped() -> None:
    assert canonicalize({"名": "值"}) == '{"名":"值"}'


def test_array_order_preserved_and_no_whitespace() -> None:
    assert canonicalize({"a": [3, 1, 2], "b": True}) == '{"a":[3,1,2],"b":true}'


def test_float_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize({"x": 1.5})


def test_null_in_array_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize({"x": [None]})


def test_fingerprint_shape() -> None:
    h = fingerprint({"nodes": [], "edges": []})
    assert len(h) == 64 and h == h.lower()
    assert short_hash(h) == h[:6]


def test_golden_cases() -> None:
    """golden 判例 = TS 镜像实现的唯一验收标准（00 §4.4）。"""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(cases) >= 8
    for case in cases:
        assert canonicalize(case["input"]) == case["canonical"], case["name"]
        assert fingerprint(case["input"]) == case["sha256"], case["name"]
