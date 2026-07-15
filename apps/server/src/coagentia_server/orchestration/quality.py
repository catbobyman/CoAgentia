"""O8 拆解质量回路（M8b L9，汇总设计 §8）：把提案落地调整/拒绝的结构化信号回流 Orchestrator，
让拆解越拆越好。信号即时逐次投、不做历史聚合注入（上下文成本与收益不成比——Agent MEMORY 是正确
载体，裁决 #9）；MEMORY 沉淀归 Agent 自管（系统从不写 Agent Home，R5/FR-3.3 边界）。

单源：信号体在 `adjustment_signal_body` 一处生成，landing 线程留痕消息与 hub `inject_guard_feedback`
直投共用同一份内容（护栏可见、人机同源）。

**REJECTED 触发点**：拒绝理由的结构化留痕已在 draft.reject_proposal 的 source 线程系统消息完成
（M6b 定论：拒绝是**被动纠正记录**，注入下次拆解上下文的线程摘要即可；**不主动 @/唤醒 Orchestrator**
——主动唤醒会诱发未经请求的重提）。故 L9 的主动直投只作用于**带调整落地**（落地已成事实，无重提风险，
让 Orchestrator 学习其提案被如何调整）。此分野是 §8.2 与 M6b 教训合流的实施裁决。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts.enums import ProposalKind


def adjustment_signal_body(proposal: dict[str, Any], landed_hash: str) -> str | None:
    """带调整落地的质量信号体（§8.1）：`landed_hash != proposal_hash` 或 `adjustments` 非空 → 结构化
    信号文本；未调整（人类原样确认）→ None（不发信号，防噪声）。decomp=调整六 op 计数、delta=剔除
    清单下标——单源，landing 留痕与 hub 直投同用。"""
    adjustments = proposal.get("adjustments") or []
    if not adjustments and landed_hash == proposal.get("proposal_hash"):
        return None
    is_delta = proposal.get("kind") == ProposalKind.DELTA.value
    label = "增量提案" if is_delta else "拆解提案"
    if is_delta:
        detail = (
            f"人类剔除了 {len(adjustments)} 个 op（原始下标 {sorted(int(i) for i in adjustments)}）"
            if adjustments
            else "落地内容与提案指纹不一致（人类微调）"
        )
    else:
        detail = (
            f"人类在确认时做了 {len(adjustments)} 处调整"
            if adjustments
            else "落地内容与提案指纹不一致（人类微调）"
        )
    return (
        f"质量信号：你的{label}（rev.{proposal.get('revision')}）落地时经人类调整——{detail}。"
        "请先复述你对该调整的理解，再把可复用的教训沉淀进 MEMORY.md（同类调整出现两次即视为拆解"
        "习惯问题）。"
    )


__all__ = ["adjustment_signal_body"]
