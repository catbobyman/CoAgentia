"""O8 拆解质量回路（M8b L9，汇总设计 §8）：带调整落地 → 结构化质量信号（单源体 + 线程留痕）。

- adjustment_signal_body：decomp 调整 / delta 剔除清单 / 未调整（None）逐例。
- 集成：delta 部分接受（剔除 op）落地 → source 线程出现质量信号系统消息 @Orchestrator。
- REJECTED 按 M6b 教训仅被动留痕不主动直投（不在此测——见 test_delta 拒绝留痕）。
"""

from __future__ import annotations

from coagentia_server.orchestration import quality


def test_adjustment_signal_body_decomp_adjusted() -> None:
    proposal = {
        "kind": "full", "revision": 2, "proposal_hash": "p",
        "adjustments": [{"op": "retitle"}, {"op": "drop_node"}],
    }
    body = quality.adjustment_signal_body(proposal, landed_hash="landed_differs")
    assert body is not None
    assert "拆解提案" in body and "2 处调整" in body
    assert "MEMORY.md" in body and "rev.2" in body


def test_adjustment_signal_body_delta_removed_ops() -> None:
    proposal = {
        "kind": "delta", "revision": 1, "proposal_hash": "p", "adjustments": [2, 0],
    }
    body = quality.adjustment_signal_body(proposal, landed_hash="q")
    assert body is not None
    assert "增量提案" in body and "剔除了 2 个 op" in body
    assert "[0, 2]" in body  # 排序后下标


def test_adjustment_signal_body_unadjusted_none() -> None:
    """人类原样确认（landed_hash==proposal_hash 且 adjustments 空）→ None，不发信号（防噪声）。"""
    proposal = {"kind": "full", "revision": 1, "proposal_hash": "same", "adjustments": []}
    assert quality.adjustment_signal_body(proposal, landed_hash="same") is None


def test_adjustment_signal_body_hash_mismatch_only() -> None:
    """adjustments 空但 landed_hash≠proposal_hash（微调）→ 仍发信号。"""
    proposal = {"kind": "full", "revision": 1, "proposal_hash": "a", "adjustments": []}
    body = quality.adjustment_signal_body(proposal, landed_hash="b")
    assert body is not None and "微调" in body
