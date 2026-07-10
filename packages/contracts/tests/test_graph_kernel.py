"""图内核测试（画布环检测/阻塞派生）+ golden 判例（TS 镜像唯一验收标准）。"""

import json
from pathlib import Path

from coagentia_contracts.kernel.graph import derive_blocked, detect_cycle

GOLDEN = Path(__file__).parents[2] / "fixtures" / "golden" / "graph.json"


def test_detect_cycle_none_on_dag() -> None:
    assert detect_cycle([], []) is None
    assert detect_cycle(["A", "B", "C"], [("A", "B"), ("B", "C")]) is None
    # 菱形汇聚无环
    assert detect_cycle(
        ["A", "B", "C", "D"], [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
    ) is None


def test_detect_cycle_returns_deterministic_path() -> None:
    assert detect_cycle(["A"], [("A", "A")]) == ["A"]  # 自环
    assert detect_cycle(["A", "B"], [("A", "B"), ("B", "A")]) == ["A", "B"]
    # 间接环：环不含引入尾 A（返回环本体）
    assert detect_cycle(
        ["A", "B", "C", "D"], [("A", "B"), ("B", "C"), ("C", "D"), ("D", "B")]
    ) == ["B", "C", "D"]


def test_detect_cycle_stable_across_input_order() -> None:
    """节点/边输入顺序不改变返回路径（内部按码点排序）。"""
    a = detect_cycle(["A", "B", "C"], [("A", "B"), ("B", "C"), ("C", "A")])
    b = detect_cycle(["C", "B", "A"], [("C", "A"), ("B", "C"), ("A", "B")])
    assert a == b == ["A", "B", "C"]


def test_derive_blocked_cascade_and_root() -> None:
    edges = [("A", "B"), ("B", "C")]
    assert derive_blocked(["A", "B", "C"], edges, set()) == {"B", "C"}  # 根 A 永不 blocked
    assert derive_blocked(["A", "B", "C"], edges, {"A"}) == {"C"}  # 级联解除一格
    assert derive_blocked(["A", "B", "C"], edges, {"A", "B", "C"}) == set()


def test_derive_blocked_diamond_join() -> None:
    edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
    # 汇聚节点 D 需两前驱皆 satisfied 才解锁
    assert derive_blocked(["A", "B", "C", "D"], edges, {"A"}) == {"D"}
    assert derive_blocked(["A", "B", "C", "D"], edges, {"A", "B", "C"}) == set()


def test_golden_cases() -> None:
    """golden 判例 = TS 镜像实现的唯一验收标准（00 §4.4）。"""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(cases) >= 11
    for case in cases:
        edges = [(a, b) for a, b in case["edges"]]
        if case["fn"] == "detect_cycle":
            assert detect_cycle(case["node_ids"], edges) == case["cycle"], case["name"]
        elif case["fn"] == "derive_blocked":
            got = derive_blocked(case["node_ids"], edges, set(case["satisfied"]))
            assert got == set(case["blocked"]), case["name"]
        else:
            raise AssertionError(f"未知判例函数: {case['fn']}")
