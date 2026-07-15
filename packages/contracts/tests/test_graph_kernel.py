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


def test_derive_blocked_partial_policy() -> None:
    """W9 双档（M8b L7）：partial 节点看 terminal_satisfied，strict 节点看 done_satisfied。"""
    edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
    ids = ["A", "B", "C", "D"]
    done = {"A", "B"}  # B done；C 仅终态非 done
    terminal = {"A", "B", "C"}
    # D=partial：C 到达终态即放行 → D 不 blocked
    assert derive_blocked(ids, edges, done, terminal, {"D": "partial"}) == set()
    # D=strict（默认/空 policy）：C 非 done → D 仍 blocked（strict 语义原样）
    assert derive_blocked(ids, edges, done, terminal, {}) == {"D"}
    assert derive_blocked(ids, edges, done, terminal) == {"D"}


def test_derive_blocked_partial_requires_all_terminal() -> None:
    """partial 非「任一完成」——仍要求全部前驱到达终态（防上游还在跑就汇总的脏读）。"""
    edges = [("A", "D"), ("B", "D")]
    ids = ["A", "B", "D"]
    # A 终态、B 仍在跑 → partial 的 D 仍 blocked
    assert derive_blocked(ids, edges, set(), {"A"}, {"D": "partial"}) == {"D"}
    # 两前驱皆终态 → 放行
    assert derive_blocked(ids, edges, set(), {"A", "B"}, {"D": "partial"}) == set()


def test_derive_blocked_backward_compatible_single_set() -> None:
    """三参调用（省略 terminal/policy）≡ 全 strict 单集合语义（既有 caller 逐字节不变）。"""
    edges = [("A", "B"), ("B", "C")]
    assert derive_blocked(["A", "B", "C"], edges, {"A"}) == {"C"}


def test_golden_cases() -> None:
    """golden 判例 = TS 镜像实现的唯一验收标准（00 §4.4）。"""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(cases) >= 11
    for case in cases:
        edges = [(a, b) for a, b in case["edges"]]
        if case["fn"] == "detect_cycle":
            assert detect_cycle(case["node_ids"], edges) == case["cycle"], case["name"]
        elif case["fn"] == "derive_blocked":
            if "done_satisfied" in case or "policy" in case:  # W9 双档判例（M8b L7）
                done = set(case["done_satisfied"])
                terminal = set(case.get("terminal_satisfied", case["done_satisfied"]))
                got = derive_blocked(
                    case["node_ids"], edges, done, terminal, case.get("policy", {})
                )
            else:  # 既有单集合判例（strict 语义，逐字节不变）
                got = derive_blocked(case["node_ids"], edges, set(case["satisfied"]))
            assert got == set(case["blocked"]), case["name"]
        else:
            raise AssertionError(f"未知判例函数: {case['fn']}")
