# M4 执行计划（M4-DEV-PLAN）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-10，M4 开工首会话（任务书 [M4-HANDOFF.md](M4-HANDOFF.md)） |
| 用途 | M4 逐模块执行计划 + 进度表 + 验收映射 + code-review 记录（体例同 [M3-DEV-PLAN.md](M3-DEV-PLAN.md)） |
| 本批范围 | **块 M4a「沉默提醒与循环 Reminder」**（F0·F1·F2·F3·F4 ＋ B-M4-1；出口清单 §9a）。块 M4b「freshness 与 HeldDraft」不与 a 交错，等 §9a 全绿再另开 |
| 编排方式 | 多 agent 并行/串行：**F0 ∥ F1 ∥ F2**（契约登记 / 0006 建表 / 挂账双修，文件域不相交）→ **F3 → F4**（同改 hub.py 不同方法，串行避冲突）→ **B-M4-1**（前端）→ 整合守门 → 实机 verify → `/code-review high` → 修复复跑 |

---

## 1. 现有资产复用锚点（已 scout 核实，勿重建）

- HeldDraft 三形状 + 枚举（entities.py:453 / enums.py:143）、202 `MessageHeld`（rest.py:261）、WS held_draft/reminder/activity 三族、guard.* 诊断 + "Draft held" 文案常量、`TaskEventKind.REMINDER_SENT/ESCALATED`、`LoopContractBody`（M3a 建）、channels 阈值列 + `ChannelPatch` 全字段、`tasks.silence_override_h/status_changed_at`、`task_contracts.reminder_id`（0003 建）——全部 M1–M3 已就位。
- `run_reminder_scan` / `_filter_gated` / 直投桥 `inject_contract_draft_request` / `gateway_tx` 提交后 flush 广播范式（hub）。
- 一致性双跑 `test_conformance_dual.py`；catalog `test_catalogs.py`；表结构 `test_schema_conformance.py`/`test_alembic_upgrade.py`。

## 2. 模块分解与执行结果（DoD 全达成）

| # | 模块 | 交付 | 落点 |
| --- | --- | --- | --- |
| **F0** | 契约登记 | `ENDPOINTS_M4`(4) held-drafts 端点、`HeldDraftReleaseResponse`/`HeldDraftResponse`、`HeldDraftRow` 增 `file_ids`/`as_task`/`HeldDraftAsTask`、`HeldDraftReasons.total_unread`、`ErrorCode.HELD_DRAFT_RESOLVED`(23)、`ReminderCreate` 删 `loop_contract_id` 增 `loop_contract`、mock `GET /held-drafts`、catalog/manifest 测试 + gen 确定 | packages/contracts + mock-server |
| **F1** | 0006 建表 | Alembic `0006_m4_held_drafts`（held_drafts 17 列 + 活动行分区唯一 `uq_held_drafts_active`(COALESCE(thread_root_id,'') where status∈held/reevaluating) + `ix_held_drafts_status`）、`HeldDraft` ORM、`M4_TABLES`；从零/增量双路 + 唯一性强制测试 | db/models.py + migrations |
| **F2** | 挂账双修 | `_emit_activity` 迁 `activity/service.py`（`emit_activity(tx, ...)` conn 注入式，hub 后台可调；广播守"提交后 flush"）；`patch_task` 白名单式 null 清除（`silence_override_h=null` 可清、不误伤 title） | activity/service.py + routes/messages.py + routes/tasks.py |
| **F3** | D5 沉默升级链 | `tasks/silence.py`（`decide`/`compute_last_activity`/`threshold_hours`，防自激 `SELF_EXCITE_EVENT_KINDS`）+ hub `run_silence_scan`/`_silence_loop`/`_silence_inputs`/`_reminder_targets`/`_emit_silence_reminder`/`_emit_silence_escalation`；三态阈值提醒（Todo→创建者/InProg→owner/InReview→频道人类）+ 升级（主流消息 + activity silence_escalation）+ 升级后静默 | tasks/silence.py + hub.py |
| **F4** | 循环 Reminder | `reminders/interval.py`（parse_interval/add_interval/next_after）；`create_reminder` 新门（recurring 必带 loop_contract 缺 422 / once 携带 422 / cadence 校验 + 一致）+ 同事务建 task_contracts 挂接行 + 回填；`run_reminder_scan` 按 kind 分支；daemon mcp `create_reminder` 扩 loop_contract 参数 | contracts/rest.py + routes/members.py + hub.py + daemon/mcp.py |
| **B-M4-1** | 前端 | RemindersTab 强化（kind/cadence/next_fire/锚点/循环·契约角标/取消）+ `api.cancelReminder` + `useCancelReminder` + wsBridge `reminder.created/updated` case；Activity 置顶接真（结构就位，F3 产出即渲染） | apps/web |

**推进**：F0∥F1∥F2 三路并行开工 → 集成全量守门绿 → F3→F4 串行（同 hub.py）→ 集成绿 → B-M4-1 → 集成绿 → 实机 verify → code-review → 修复。

## 3. `/code-review high` 结论与修复（10 CONFIRMED + 2 PLAUSIBLE）

8 角度 × 25 agents。10 CONFIRMED 收敛为 **1 正确性 + 一簇 hub 清理/效率**，明细与处置见 [M4A-EVIDENCE.md §5](../verify/M4A-EVIDENCE.md)：

1. **正确性（必修）**：recurring reminder 重放风暴——旧实现每轮 +1 interval，① 建即触发、② 停机漏 K 周期后逐格重放洪泛。修：`interval.next_after` O(1) 塌缩到 >now 下一网格点 + 创建 recurring 时 next_fire_at=now+interval。
2. 复用/altitude（3 合一）：系统消息发射骨架三处拷贝 → 抽 `_post_system_message`。
3. 简化/效率（2 合一）：_silence_inputs 三条 func.max → 单查询条件聚合。
4. 效率：run_silence_scan channel N+1 → distinct IN 批取。
5. 效率：run_reminder_scan 回读 reminder 行 → 内存拼载荷；顺手 `ledger.service.format_iso` 单源（interval 复用）。
6. PLAUSIBLE 健壮性（**评估后不采纳**）：run_silence_scan per-task SAVEPOINT 隔离——与 tx.emit 提交后 flush 解耦会产"幽灵事件"，单事务原子 + 下轮重试更安全（引用目标已 removed_at 过滤，持久失败极不可能）。

## 4. 进度表（M4a）

| 模块 | 状态 | 守门 |
| --- | --- | --- |
| F0 契约登记 | ✅ | contracts/mock 61 passed、gen 确定 |
| F1 0006 建表 | ✅ | 迁移/表结构 41 passed、双路 + 唯一性 |
| F2 挂账双修 | ✅ | activity 回归零变化、patch null 清除 |
| F3 D5 沉默链 | ✅ | test_silence 19 例 + 集成 |
| F4 循环 Reminder | ✅ | 门/挂接/重排/interval 全覆盖 |
| B-M4-1 前端 | ✅ | vitest +13（wsBridge.reminder 6 + RemindersTab 7） |
| 集成守门 | ✅ | 后端 **542 passed/3 skipped** · vitest **89** · pyright **0** · ruff · gen 确定 · web build |
| 实机 verify | ✅ | 隔离 uvicorn 8799 探针 **16/16** + 3 截图 + console 0（[M4A-EVIDENCE.md](../verify/M4A-EVIDENCE.md)） |
| code-review | ✅ | 10 CONFIRMED 全处理（1 正确性修 + 一簇清理修 + 1 PLAUSIBLE 评估不采纳），复跑绿 |

## 5. 验收映射（M4-HANDOFF §9a 出口清单）

§9a 1–7 全绿：契约登记 / 0006 建表双路 / 挂账双修零回归 / D5 全路径（三态 + 自激防护 + 升级后静默 + 重置）/ 循环 Reminder（门 + 挂接 + interval 重排）/ B-M4-1 页签+wsBridge+置顶 / 守门全绿 + 文档同步。**块 M4a 收口**；接续 = 块 M4b（freshness 门 + HeldDraft 三键 + G4/G5，另开会话，见 M4-HANDOFF §9b）。

---

## 6. 块 M4b「freshness 与 HeldDraft」执行结果（2026-07-10，收口 = PRD M4 出口）

| 项 | 内容 |
| --- | --- |
| 编排 | Workflow 多 agent 并行：**后端 F5→F6 串行**（同改 server 文件保语义连贯）**与前端 B-M4-2 并行**两条流，各自测；集成守门（主循环）→ 实机 verify → `/code-review high`（8 角度 × ≤6 → 对抗性 verify → 17 CONFIRMED）→ 修复复跑 |

### 6.1 模块交付（DoD 全达成）

| # | 模块 | 交付 | 落点 |
| --- | --- | --- | --- |
| **F5** | freshness 门 + 三键端点 + G5 rehold 升级 | `guard/service.py`（compute_unread/hold_or_update SAVEPOINT 兜并发/_escalate/freshness_hold 总装/write_guard_diagnostic/ACTIVE·TERMINAL 单源）；messages.py 门（仅 agent、既有校验+幂等 hit 后、落库前）+ 抽 `persist_message` 落库核心（post/release 共用保零行为差）；routes/held_drafts.py 四端点（GET 默认活动态 / release 原载荷不依赖 daemon / discard 直投 503 回滚 / reevaluate 委托 hub 死锁规避）；hub `inject_guard_feedback`/`_held_reevaluation_combo`/`reevaluate_held` | guard/ + routes/ + hub.py |
| **F6** | G4 定时 + GC 豁免 | hub `run_held_scan`+`_held_loop`（held_interval，在线先探再翻 reevaluating，提交后组合含 deliver 推进游标防复扣）；gc.py 活动 held file_ids 豁免 | hub.py + files/gc.py |
| **F7** | 端到端实机 verify | 隔离 uvicorn 8801 + 真 websockets daemon-sim + 浏览器 WS；探针 **38/38** + playwright 3 截图（[M4B-EVIDENCE.md](../verify/M4B-EVIDENCE.md)） | docs/verify |
| **B-M4-2** | HeldDraft 卡 + wsBridge | HeldDraftCard（草稿折叠/未读跳转/本地读秒倒计时/三键/升级横条/终态回执/409 刷新/非-409 toast）+ HeldDraftList 归位 + ChannelChatScreen/ThreadPanel 挂载 + wsBridge held_draft.* + api/queries 三键 | apps/web |

### 6.2 `/code-review high` 结论与修复（明细见 [M4B-EVIDENCE.md §5](../verify/M4B-EVIDENCE.md)）

**5 正确性/健壮性修复 + 1 簇清理 + 2 DRY 债挂账**：#4 幂等前置（ledger.lookup，防重放误扣 → 重复消息）/ #5 reevaluate 终态守卫（防并发复活丢弃草稿）/ #6 G4 离线先探（防永卡 reevaluating）/ #1 清单默认活动态（防活动 held 被终态挤到后页）/ #3 三键 503 toast / #7·#10 诊断走 helper；#2 由 #6 覆盖；#8·#9 系统消息骨架 / human_members 跨模块 DRY 债挂账（M4-HANDOFF §8）。

### 6.3 进度表（M4b）

| 模块 | 状态 | 守门 |
| --- | --- | --- |
| F5 门+三键端点 | ✅ | test_held_drafts freshness/三键/G5 全覆盖 |
| F6 G4+GC | ✅ | G4 在线/离线/排除升级 + 死循环反例 + GC 豁免/回收 |
| B-M4-2 前端 | ✅ | vitest HeldDraftCard 11 + wsBridge.heldDraft 6 |
| 实机 verify | ✅ | 探针 38/38 + 3 截图 + console 0 |
| code-review | ✅ | 17 CONFIRMED → 5 修 + 1 簇清理 + 2 挂账，复跑绿 |
| 集成守门 | ✅ | 后端 **572 passed/3 skipped** · vitest **106** · pyright 0 · ruff · gen 确定 · 双 build |

**块 M4b 收口 = M4 里程碑完成**（§9b 出口清单 8–14 全绿）。
