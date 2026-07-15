# M8 上线前实机测试 — 第一阶段实测发现（真 CLI 多 Agent）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-14（真机会话，Owner 授权自主推进） |
| 环境 | 真 uvicorn server(8787) + 真 web(5173) + 真 daemon + 真 claude(2.1.208)/codex(0.144.0) CLI；默认库迁移 0011→0012（备份 `coagentia.pre-m8-migrate.*.db`） |
| 定位 | 首次以**真 CLI**（非 daemon-sim）跑 Orchestrator 拆解链。历届 M6/M7/M8 verify 全用 FakeAdapter，本次是真机空白的首次填补。方案见 [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) T-A′ |
| 一句话结论 | **拆解→落地全链在真 CLI 下完整跑通（含修复循环、O8 汇总节点、浏览器真机确认落地）；交付执行卡在「实现者角色 agent 的任务投递唤醒」，与多个真机专属摩擦一并记录。** |

## 1. 验证成功（真 CLI 首次）

| 环节 | 结果 | 证据 |
| --- | --- | --- |
| 环境搭建 | ✅ | daemon 探测本机真实模型：claude `[opus-4-8/sonnet-4-5/haiku-4-5]`、codex 7 个（gpt-5.6-sol 等）——**再次印证 #1 修复价值**（probe 有全表，旧弹窗却让人手填） |
| Orchestrator 拆解 | ✅ | Orch-Main(真 claude opus) 被 @消息唤醒，产出 3 节点 decompose 提案 |
| **智能识别重复需求** | ✅ | 先后发中文+英文两版同需求，Orch 自判「同一需求，按最新英文为准」，不重复建案 |
| 规模判断 + 依赖分析 | ✅ | 判定 decompose（3 节点）、①→②→③ 严格串行链、跨两角色分工 |
| **修复循环（J8 真机首验）** | ✅ | 首次提交被校验打回（7 个 AC 缺 `verify_ref` 必填）→ 自动补齐 → rev.2 重发通过（repair_count=1）→ awaiting_confirm |
| 草稿确认层 UI | ✅ | 浏览器真机打开草稿层 overlay，3 节点 + N1→N2→N3 边 + owner combobox + 确认条，点击「确认落地」 |
| 落地（202 异步增量） | ✅ | proposal→landed，canvas 建 **5 节点 4 边**：3 实现任务 + merge 系统节点(idle) + **O8 汇总节点**(owner=Orch-Main，自动追加) |

## 2. 真机发现（daemon-sim 测不到，本次核心价值）

### F-1 [中] 实现者角色 agent 不响应消息/手动激活的唤醒（交付卡点）
- **现象**：N1 set 为 in_progress、owner=Dev-Codex-A 后，@消息 + 4 分钟等待，Dev-Codex-A 零响应（diag seq 不增、worktree 不派生）。对照实验：Dev-Claude-A（claude 实现者）同样零响应；而 **Orch-Main（Orchestrator）@消息唤醒正常**。
- **判定**：非 runtime 特有（claude/codex 实现者都不响应），是**角色差异**——Orchestrator 有 @顶级消息拆解入口，实现者的正确唤醒路径应是 **gating 满足后的任务投递批**，普通 @消息与手动 API `set status` 都不触发实现者 turn。
- **待查**：手动 API 激活（绕过 agent 自主 claim）是否漏触发投递副作用；或 agent 经 restart 后投递唤醒失效。需读 daemon 投递/gating 机制确认。
- **与 F-3 联动（很可能是根因链）**：落地时 3 任务 owner=None（F-3），投递唤醒**没有目标成员** → 实现者从未在落地那一刻被投递。我事后手动 `assign` 补 owner，但 assign 不重放落地时的投递窗口。**若拆解正确落 owner（修 F-3），落地即投递唤醒 owner 实现者，交付大概率自然启动**——建议先修 F-3 再复验 F-1。

### F-2 [中] agent status 卡在 busy，需 restart 才回 idle
- **现象**：三个 agent onboarding turn 后 status 恒为 busy，diag seq 停止增长（turn 已结束）却不回滚 idle。`lifecycle restart` 后恢复 idle。
- **影响**：busy 卡住会误导「agent 在忙」，且可能阻塞新任务分派。反复出现（每个 agent 都中招）。
- **待查**：daemon 是否漏发 turn 结束的 idle 上报；或 held draft 挂起让 status 悬挂。

### F-3 [中] 拆解角色分工写在提案正文，未落结构化 owner 字段
- **现象**：Orch-Main 在提案正文写明「N1/N2→Dev-Codex-A、N3→Dev-Claude-A」，但 decomposition.v1 节点结构里 owner 字段为空 → 落地后 3 任务 owner=None（待认领）。草稿层 combobox 显示的 owner 也未随「确认落地」提交。
- **影响**：落地任务全部待认领，需人工补指派，破坏「拆解即分工」体验。
- **待查**：① Orchestrator 话术是否应引导填结构化 owner；② 草稿层 combobox 的 owner 选择为何未进 confirm 请求体。

### F-4 [低] onboarding 问候 × freshness 门交互
- **现象**：agent 上线在 #all 打招呼，但频道有历史未读 → 问候草稿被 freshness 门 held 扣留，未发出。
- **说明**：设计内行为（freshness 门），但对「首次上线」体验是噪音。关闭 onboarding_greeting 开关可规避。

## 3. 交付执行链（未通，卡于 F-1）

拆解落地后，交付全链（worktree 派生 → agent 写码 → TaskHandoff → check → merge）**未跑通**，卡在 F-1：实现者未被唤醒干活。手动尝试（发消息×2、claim[得 CLAIM_RACE 409 印证 owner 已设]、set status 激活[N1→in_progress 但无 worktree/无 agent 活动]）均未能替代正常的任务投递唤醒。

**注**：交付执行链本身在 M6a 已由 daemon-sim verify 20/20 覆盖；此处是真 CLI 首次，卡点在唤醒机制而非交付逻辑本身。

## 4. 下一步建议

1. **F-1 优先**：读 daemon 投递/gating（`computers/hub.py` 投递批 + `system_nodes` gating），确认实现者唤醒的正确触发路径，验证「gating 满足 → 投递唤醒 owner → 派 worktree → 写码」在真 CLI 下是否本就工作、还是我的手动激活路径绕过了它。最干净的复现 = 从头走 agent 自主 claim（而非 API 手动 set status）。
2. **F-2/F-3** 值得各开一个排查任务（busy 悬挂上报、拆解 owner 落字段）。
3. 待 F-1 通后再按 [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) 铺开 T-D/T-G/T-B/T-C/T-E/T-H/T-F′ 与 6 窗口规模。

## 5. 复现锚点

- 库：`~/.coagentia/server/coagentia.db`（0012，含 realtest 频道 + Orch-Main/Dev-Claude-A/Dev-Codex-A + pomodoro-demo project）
- computer=RealTest-PC api-key=`cak_01kxhxqa0hvzbkr0f4d8hgpyfa`；realtest channel=`01KXHXW27P1P1D5G94H1V5HA04`
- proposal 落地=`01KXHYWQ7FEHNP5C2P2ZRB6VV0`（landed）；N1 task=`01KXHZQQ6B0QYSJPEAWZGMZZRC`（in_progress, owner=Dev-Codex-A）
