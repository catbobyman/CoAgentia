# M8 上线前实机测试 — 第一阶段实测发现（真 CLI 多 Agent）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-14（真机会话，Owner 授权自主推进） |
| 环境 | 真 uvicorn server(8787) + 真 web(5173) + 真 daemon + 真 claude(2.1.208)/codex(0.144.0) CLI；默认库迁移 0011→0012（备份 `coagentia.pre-m8-migrate.*.db`） |
| 定位 | 首次以**真 CLI**（非 daemon-sim FakeAdapter）跑 Orchestrator 拆解 + 交付链。历届 M6/M7/M8 verify 全用 FakeAdapter，本次填补真机空白。方案见 [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) T-A′ |
| 一句话结论 | **拆解→落地→@建议人唤醒全链在真 CLI 下设计正确且跑通；交付执行卡在一个真机 blocker：实现者 agent 的 daemon-side present 卡在 STARTING 未达 IDLE，导致 server 投递层短路、实现者永不接收唤醒。此 bug 被历届 FakeAdapter（直接报 idle）完全掩盖。** |

## 1. 验证成功（真 CLI 首次）

| 环节 | 结果 | 证据 |
| --- | --- | --- |
| 环境搭建 | ✅ | daemon 探测本机真实模型：claude `[opus-4-8/sonnet-4-5/haiku-4-5]`、codex 7 个（gpt-5.6-sol 等）——**再次印证 #1 修复价值**（probe 有全表，旧弹窗却让人手填） |
| Orchestrator 拆解 | ✅ | Orch-Main(真 claude opus) 被 @消息唤醒，产出 3 节点 decompose 提案 |
| **智能识别重复需求** | ✅ | 先后发中文+英文两版同需求，Orch 自判「同一需求，按最新英文为准」，不重复建案 |
| 规模判断 + 依赖分析 | ✅ | 判定 decompose（3 节点）、①→②→③ 严格串行链、跨两角色分工，`suggested_owner` 结构化填 N1/N2→Dev-Codex-A、N3→Dev-Claude-A |
| **修复循环（J8 真机首验）** | ✅ | 首次提交被校验打回（7 个 AC 缺 `verify_ref` 必填）→ 自动补齐 → rev.2 重发通过（repair_count=1）→ awaiting_confirm |
| 草稿确认层 UI | ✅ | 浏览器真机打开草稿层 overlay，3 节点 + N1→N2→N3 边 + owner combobox + 确认条，点击「确认落地」 |
| 落地（202 异步增量） | ✅ | proposal→landed，canvas 建 **5 节点 4 边**：3 实现任务 + merge 系统节点(idle) + **O8 汇总节点**(owner=Orch-Main，自动追加) |
| 「已落地」@建议人唤醒 | ✅ | 系统消息「#3 数据层…已激活，建议认领：**@Dev-Codex-A**」正确 @了 N1 的 suggested_owner（激活节点=无上游） |

## 2. 交付 blocker（真机专属，FakeAdapter 掩盖）

### B-1 [高·上线前必修] 实现者 agent boot 卡在 STARTING，投递层永久短路
**现象链（逐步实测）**：
1. 落地后实现者 read_position 停在落地时刻（`01KXHZQQ…`），此后**所有消息（含「已落地」@唤醒 + 我多次 @消息）零投递**——read_position 从不推进，agent 不起 turn。对照：Orchestrator（Orch-Main）read_position 正常推进、@消息正常起 turn。
2. 定位到投递层 [hub.py:1502-1503](../../apps/server/src/coagentia_server/computers/hub.py:1502)：`status = conn.present.get(agent_id); if status not in _DELIVERABLE: continue`——present 不在 `{IDLE,BUSY}` 则彻底不投不唤醒。
3. 根因：`_RESUMABLE={STARTING,IDLE,BUSY}` 但 `_DELIVERABLE={IDLE,BUSY}`（[hub.py:186-187](../../apps/server/src/coagentia_server/computers/hub.py:186)）。**实现者 boot 卡在 STARTING**（在 present 里但不可投递），未完成到 IDLE 的握手。
4. `agents.status`（DB）由 `_report_status_changed` 无条件写（[hub.py:1215](../../apps/server/src/coagentia_server/computers/hub.py:1215)），而 lifecycle restart 也会写 DB → **DB 显示 idle 是假象**，与 daemon 真实 present（STARTING）不一致，误导排查。
5. 排除项：不是我手动 set N1 in_progress 造成（撤销 unclaim 回 todo + restart 后仍不 deliverable）；不是 codex 特有（claude 实现者 Dev-Claude-A 同样卡）；不是时序（干净 restart + 30s 等待 + @消息仍零响应）。

**为何 Orchestrator 正常而实现者卡**：待 daemon 侧诊断——两者同 daemon 同 computer，差异在 boot 握手是否走到 IDLE。可疑面：实现者 boot 时的工作目录/激活上下文注入、codex app-server 握手、或经多轮 onboarding-held+restart 后的 daemon 会话状态。**当前 daemon 日志不写 stdout（background output 空），运行时 present 不可观测，是继续定位的最大障碍——建议先给 daemon 加可读日志。**

**影响**：真 CLI 下**交付链完全无法启动**——实现者收不到任务唤醒，worktree 不派生、代码不写。这是"从对话到上线"承诺在真机上的断点。

### B-2 [中] agent status 结束后卡 busy / boot 卡 starting，DB 与 present 双源不一致
`_report_status_changed` 是 DB status 唯一写入方，但 lifecycle 操作也写 DB，导致 DB `agents.status` 与 daemon `conn.present` 可能不一致。排查时 DB 查到 idle 但实际 present 是 starting/被 pop，严重误导。**建议**：只读 present 判定可投递性时，提供一个诊断端点暴露 `conn.present`，或让 DB status 严格镜像 present。

### B-3 [低] onboarding 问候 × freshness 门交互
agent 上线在 #all 打招呼，但频道有历史未读 → 问候草稿被 freshness 门 held 扣留未发出。设计内行为，但对首次上线是噪音；关 `onboarding_greeting` 可规避。

## 3. 澄清：suggested_owner「待认领」是设计，非 bug

排查中一度误判「拆解分工没落 owner」为缺陷。实为**裁决 O4「建议不锁定」**（[landing.py:392-393](../../apps/server/src/coagentia_server/orchestration/landing.py:392) 原文）：`suggested_owner` 落地语义 = 任务 owner 恒 None、建议人选进「已落地」消息 @唤醒话术、**claim 是唯一认领通道**（防重）。Orch 已在 `suggested_owner` 结构化字段正确填人，「已落地」消息也正确 @了建议人——设计完整，只是被 B-1 卡在"建议人收不到唤醒"。

## 4. 下一步建议

1. **B-1 优先**（上线前 blocker）：先给 daemon 加可读日志（写文件或 stdout flush），复现实现者 boot，观测其 present 停在 STARTING 的哪一步；对比 Orchestrator boot 到 IDLE 的路径差异。修复方向 = 让实现者 boot 握手正常走到 IDLE。
2. **B-2**：加 present 诊断可见性 + DB/present 一致性。
3. 待 B-1 通后，从头走**纯自动流程**（不手动 assign/set，靠「已落地」@唤醒 → agent 自主 claim）复验交付全链，再按 [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) 铺开 T-D/T-G/T-B/T-C/T-E/T-H/T-F′ 与 6 窗口规模。

## 5. 复现锚点

- 库：`~/.coagentia/server/coagentia.db`（0012，含 realtest 频道 + Orch-Main/Dev-Claude-A/Dev-Codex-A + pomodoro-demo project）
- computer=RealTest-PC api-key=`cak_01kxhxqa0hvzbkr0f4d8hgpyfa`；realtest channel=`01KXHXW27P1P1D5G94H1V5HA04`
- proposal 落地=`01KXHYWQ7FEHNP5C2P2ZRB6VV0`（landed）；N1 task=`01KXHZQQ6B0QYSJPEAWZGMZZRC`（已 unclaim 回 todo/owner=None）
- 投递层根因锚点：`hub.py` `_deliver_message`(1478) / `_compute_trigger`(1605) / `_report_status_changed`(1208) / `_RESUMABLE`·`_DELIVERABLE`(186-187)
