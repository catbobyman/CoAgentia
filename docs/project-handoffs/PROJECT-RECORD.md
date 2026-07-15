# CoAgentia 项目记录

> 本文汇总已过时的交接文档，保留项目演进事实，但不作为当前任务入口。当前状态以 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) 为准。

## 1. 设计与品牌阶段

来源：[archive/HANDOFF.md](archive/HANDOFF.md)

- 完成 Afterglow 设计系统及七批设计稿，建立品牌、交互与视觉基线。
- 项目名称由 AgentHub 统一调整为 CoAgentia，并记录镜像设计稿的同步纪律。
- 当时“仅有文档和设计稿、尚无产品代码”的判断只适用于该阶段，现已失效。

## 2. M1 契约阶段

来源：[archive/M1-HANDOFF.md](archive/M1-HANDOFF.md)

- 冻结实体模型、REST API、浏览器 WebSocket、daemon-server 协议和 Claude Code 适配器五类契约。
- 建立 Pydantic contracts、fixtures、mock server、TypeScript 生成链路和 P1 会话屏形状验证。
- 该任务书已完成归档；其中“下一步从契约实施开工”的描述已被后续实现结果取代。

## 3. M1 实现阶段

来源：[archive/M1-IMPL-HANDOFF.md](archive/M1-IMPL-HANDOFF.md)

- 完成 A1-A8 后端链路：数据层、REST、浏览器 WS、daemon 网关与对账、daemon 本体、Claude Code 适配器和端到端集成。
- 完成 B1-B2a 与 B4 前端链路，交付七屏、同源部署和关键会话流程。
- 实机完成两个 Claude Haiku Agent 的频道对话、文件产出与 reminder 验收，M1 出口清单收口。
- 该任务书中的未勾选开工指令和阶段性待办均为历史快照，不再代表当前进度。

## 4. M1 首次收口快照

来源：[archive/SESSION-HANDOFF.md](archive/SESSION-HANDOFF.md)

- 记录 M1 实现收口、代码审查修复和 `230 passed, 2 skipped` 的阶段性验证结果。
- 当时建议下一步合并 `m1-impl`、补 B2b/B3 或进入 M2。
- 该快照早于随后进行的 M1 hardening；测试数字、风险列表和下一步已由当前交接更新。

## 5. M1 Hardening 与实机复验

来源：[CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) 与 [修复报告](../m1-review-fixes-20260709/FIX-REPORT.md)

- 针对实机验证和代码审查发现的七项问题完成修复，覆盖进程参数、API/WS 基址、消息/文件一致性、错误传播及回归测试。
- 完成桌面与移动视口的真实浏览器复验，并保存截图和 trace 证据。
- 当前验证基线：Python `238 passed, 2 skipped`，Web `10 passed`，TypeScript typecheck/build 与 Ruff 通过。
- Pyright 仍有 109 个既有错误，作为独立技术债保留。
- 修复以 `351684a` 提交，并通过合并提交 `f2c993f` 合入 `main`。

## 6. M2 任务与看板（含二轮 review 修复）

来源：[archive/M2-HANDOFF.md](archive/M2-HANDOFF.md) · [M2-DEV-PLAN.md](M2-DEV-PLAN.md) · [二轮证据](../verify/M2-REVIEW2-EVIDENCE.md)

- 两块竖切完成 L1 任务协作：C0-C7 后端（契约登记/0002 建表/任务域 8 端点/task#n 解析/Activity 生成/files·search·activity 端点/MCP 六工具/usage 富化）+ 前端 B 线（P1/P5/P3/P11/P4/P8/P9/P10 接真）。
- PRD M2 出口（番茄钟全流程）真 HTTP 17/17 + 浏览器全流程实证；纪律 7 落地（TASK_TRANSITIONS/UNCLAIMABLE_STATUSES 生成到 contracts-ts，两侧共同消费）。
- 中文 FTS 结论回写契约 A §10.4（unicode61 不命中 CJK 子串，trigram 归 M3）。
- 收口后二轮实机 verify 35/35 + `/code-review high`（15 CONFIRMED）修复批：安全（activity done 归属门）、数据完整（tasks 分页聚合）、输入法（IME 误发）、时区（UTC 硬切统一 lib/time.ts）、实时性（channelFiles/usage 失效）等 15 项修复 + 契约 A v1.0.3（ActivityItemPublic.actor_member_id 派生字段）。
- 最终基线：后端 387 passed / 3 skipped，前端 vitest 18，双侧 typecheck/build/ruff 绿。
- 提交链：`42f20f0`（C0-C2）→ `ba73f72`（hardening）→ `6c12b90`（M2 后半）→ `cdb27db`（二轮 review）。

## 7. M3a 契约与校验（块 M3a 收口 2026-07-10）

来源：[M3-HANDOFF.md](archive/M3-HANDOFF.md) · [M3-DEV-PLAN.md](M3-DEV-PLAN.md) · [M3A-EVIDENCE.md](../verify/M3A-EVIDENCE.md)

- 多 agent 工作流编排：地基 **E0（契约登记）∥ E1（建表迁移）并行** → 实现 **E2+E3（契约端点+T7）后端 ∥ B-M3-1（契约卡）前端并行** → 整合守门 → 实机 verify → `/code-review high`。文件域不相交，无冲突。
- **E 契约 v1.2 先行**：M3 契约面**零新 Agent 工具**（提交/force-start 人确认·C3 门，读走 get_task、起草走 request-draft 直投 + send_message 贴线程；画布结构编辑工具位随 M6）——engineering_docs/05 §3 裁决表。
- **L2 契约链路**：task_contracts 落库（0003_m3 三表一次建齐）+ TaskPlan/TaskHandoff/LoopContract body 模型（PRD §4.3 v1，M1 原缺）+ 提交/修订链（新 revision、旧行 superseded）+ request-draft S1 直投（`message.inject` + `contract_draft_request`）。
- **T7 流转门**：level=l2 置 in_review 校验活动 TaskHandoff deliverables/evidence 非空 → 422 HANDOFF_INCOMPLETE{missing}（人与 Agent 同拒）；deliverables/evidence 非空由 T7 执法（提交期允许空，可增量起草）。
- **升格 P-2**：PATCH /tasks/{id} 扩 level，仅 l1→l2 单向；l2→l1 拒 422 D1。
- **前端 P5 契约卡接真**：TaskPlan/TaskHandoff/revision/历史版本渲染 + 「让 @Agent 起草」入口 + T7 就地提示（交互 §5.4）；引入 happy-dom + testing-library 建首个组件渲染测试。
- **`/code-review high`（8 角度 finder）**：CONFIRMED 全修（6 正确性 + 3 质量 + 回归测试）：修订链竞态（分区唯一索引 `uq_task_contracts_active` + SAVEPOINT 重试）、loop_contract 挂 Task（TASK_CONTRACT_KINDS 门）、T7 经升格绕过（patch_task 补守护）、前端跨任务陈旧态（ThreadPanel key）、body 断言崩溃防御、T7 错误静默吞兜底、task_id 索引、占位文案收敛、契约 body 单测缺口。
- **收口基线**：后端 **421 passed / 3 skipped**（387→+34），前端 vitest **23**，双侧 typecheck/build、ruff、`pnpm gen` 两跑一致全绿。实机：真 HTTP 16/16 + 2 新守卫 + 浏览器契约卡/T7 就地提示截图。
- **块 M3b（画布与 gating）未开工**——按纪律不与 a 交错，另开会话按 M3-HANDOFF §5/§9b。

## 8. 挂账清理批（2026-07-10，M3a 与 M3b 之间的独立三批）

| 批 | 内容 | 提交 |
| --- | --- | --- |
| 批1 附件卡数据源 | 契约 A **v1.0.4**：`MessagePublic` 增读面派生 `files`（Public≠Row 第 5 例）；消息读面（列表/线程/响应/搜索）+ `message.created` 广播附着；0004 files 索引；前端 `m.files` 直消费删 `filesByMessage`。实机 9/9 + 截图（[B1-ATTACH-EVIDENCE.md](../verify/B1-ATTACH-EVIDENCE.md)） | `58b89b5` |
| 批2 keyset 分页 | `_pagination.keyset_page`（(created_at,id) 行值锚点 + LIMIT 下推）统一 messages/tasks/files/activity；messages before 修成紧邻回翻；ActivityScreen 'all' 单拉 + 客户端过滤、wsBridge 删多档 patch。实机 10/10 + 单请求截图（[B2-KEYSET-EVIDENCE.md](../verify/B2-KEYSET-EVIDENCE.md)） | `9331698` |
| 批3 pyright 清零 | 133 → **0**（`models.tbl()` Table 窄化 80×、`models.row_dict()` 非空窄化、daemon/api 零星标注）；pyright 并入根级 `pnpm typecheck` 守门 | 本批 |

收口基线：后端 **428 passed / 3 skipped**、web vitest 23、ruff / pyright / gen 确定 / 双侧 build 全绿。

## 9. M3b 画布与 gating（块 M3b 收口 2026-07-10 = PRD M3 出口达成）

来源：[M3-HANDOFF.md](archive/M3-HANDOFF.md) §9b · [M3-DEV-PLAN.md](M3-DEV-PLAN.md) §7 · [M3B-EVIDENCE.md](../verify/M3B-EVIDENCE.md)

- 多 agent 工作流编排：**E0b 契约地基先行**（canvas 请求/响应模型 + `kernel/graph.py` 图内核 detect_cycle/derive_blocked + `golden/graph.json` + mock 读形状 + pnpm gen）→ 三轨并行（**后端 E4→E5 ∥ 前端 B-M3-2→B-M3-3 ∥ FTS trigram**，文件域不相交）→ 守门汇总 → E6 实机（主 loop 亲为）→ `/code-review high`（8 角度工作流）→ 修复 → 复跑。
- **E4 画布结构端点**：`routes/canvas.py` + `canvas/service.py`——快照/nodes CRUD（agent 节点=第三创建途径：锚点系统消息 + create_task level=l2）/edges CRUD（写事务内拓扑排序，成环 GRAPH_CYCLE）/layout（不 bump）；每写串行化点 + baseline bump + `canvas.baseline_advanced` 广播（契约 A §6 规范快照，复用 `kernel/fingerprint`）；目录 vs 实 serve 一致性（retry 归 M6 不 serve）。
- **E5 blocked gating + force-start**：blocked = 画布边 + 上游任务/系统状态**实时推导不落库**（裁决 2，图内核权威在 server，纪律 8）；gating 作用于**投递层**——`_deliver_message` + `reconcile` 两处决策点对 blocked 任务线程消息压制唤醒**并从投递批剔除**（read_position 水位截到首个 gated 之前，不消费）；`force-start` 仅人类（Agent 403 rule=C3）、双留痕（task_events + 系统消息）、不改状态不删边、hub `_run_sync` 桥本次放行；status 写不受 gating 限（R4/R7）。
- **前端 B-M3-2/3**：React Flow（@xyflow/react）画布——节点实时着色 + blocked 级联徽标 + 系统菱形；连边成环 TS 预判（`lib/graph.ts` 镜像，与 `golden/graph.json` 对照）+ 服务端 GRAPH_CYCLE 复核；布局防抖 PUT（拖拽中不回弹）；深链 ?node= 双向；wsBridge 7 个 canvas.* handler。升格补契约弹层 + ForceStartModal 二次确认（P13b）+ 看板 P3/P11 blocked 徽标（共享 `deriveCanvasBlocked`）。
- **FTS trigram**（浮动件收口）：`0005` 迁移 unicode61→trigram（双路 + downgrade）；**3 字符地板**——≥3 字连续 CJK 走 MATCH、<3 字走正文/锚点 LIKE 兜底（元字符转义）；结论回写契约 A §10.4；GET /search 形状不变。
- **`/code-review high`**（8 角度 → 24 候选 → 10 CONFIRMED）**全修 + 4 回归测试**：① gating 投递批泄漏（gated 消息经无关触发被投+消费）→ 过滤 gated + 水位截断，解锁补投不丢；② patch_node 清空 check command 绕 V14 → 复校门；③ LIKE 元字符未转义（q=%匹配全部）→ `_like_pattern` 转义；④/⑤ tasks 锚点 <3 字未 LIKE 兜底 → 补；⑥/⑩ blocked_task_ids 全库扫描 → 收窄单画布 `is_task_blocked`；⑦ 拖拽中 WS 回弹坐标 → draggingRef 守卫；⑧ satisfied 前后端复制 → 抽 `graph.deriveCanvasBlocked`；⑨ wsBridge helper 重复 → 去重。
- **收口基线**：后端 **483 passed / 3 skipped**（428 → +51 M3b + 4 review 回归，零回归）、web vitest **76**（23 → +53）、pyright 0（并入 typecheck）、ruff 干净、`pnpm gen` 确定、双侧 build 绿。
- **实机 verify**：真 uvicorn 8799 + **真 websockets daemon-sim** 工程三角六节点 DAG **17/17**（成环拒 / T7 门 / force-start 403·留痕 / **blocked 不唤醒·上游 done 解锁·force-start override 唤醒且真事件循环无死锁** / R4·R7 状态写不受限）+ 浏览器同源 6 截图（含 **WS 无刷新实时解锁**：#8 done → #9 blocked 徽标实时消失）+ console 0 错误（[M3B-EVIDENCE.md](../verify/M3B-EVIDENCE.md)）。
- **M3 里程碑收口**：§9a + §9b 全绿 = **PRD M3 出口达成**。M3-HANDOFF 移入 archive/（README 约定 3）。

## 10. M4a 沉默提醒与循环 Reminder（块 M4a 收口 2026-07-10）

来源：[M4-HANDOFF.md](M4-HANDOFF.md) §9a · [M4-DEV-PLAN.md](M4-DEV-PLAN.md) · [M4A-EVIDENCE.md](../verify/M4A-EVIDENCE.md)（提交 `01ff2d1`）

- **契约修订先行**（纪律 1，随 M4 立项 `e177328` 落笔）：A **v1.0.5**（held_drafts 增 file_ids/as_task 载荷列 + reasons 上限 50/total_unread + 活动行分区唯一）· B **v1.2**（§10 护栏与沉默提醒规范条文 + §4.14 held 三键端点 + POST /reminders 内联 loop_contract + HELD_DRAFT_RESOLVED 23 码）· D **v1.0.1**（重评估组合 wake+deliver+inject 防复扣死循环 + staging GC held 豁免）· E **v1.3**（M4 零新 Agent 工具，create_reminder 扩 loop_contract）。
- **编排**：**F0 契约登记 ∥ F1 0006 建表 ∥ F2 挂账双修** 三路并行（文件域不相交）→ 集成守门绿 → **F3 D5 沉默链 → F4 循环 Reminder** 串行（同 hub.py 不同方法）→ 集成绿 → **B-M4-1 前端** → 实机 verify → `/code-review high` → 修复复跑。
- **F0**：ENDPOINTS_M4(held-drafts 4) + held 干预响应模型 + HeldDraft 载荷/reasons 字段 + HELD_DRAFT_RESOLVED + ReminderCreate 删 loop_contract_id/增 loop_contract + mock GET /held-drafts + gen 确定。
- **F1**：Alembic `0006_m4_held_drafts`（held_drafts 17 列 + `uq_held_drafts_active`（COALESCE(thread_root_id,'') where status∈held/reevaluating）+ ix_held_drafts_status）+ HeldDraft ORM + M4_TABLES；从零/增量双路 + 唯一性强制测试。
- **F2 挂账双修**：`_emit_activity` 迁 `activity/service.py`（`emit_activity(tx, ...)` conn 注入式——hub 后台无 request 上下文可调，广播守"提交后 flush"不变量）；`patch_task` 白名单式 null 清除（silence_override_h=null 可清、不误伤 title）。
- **F3 D5 沉默升级链**：`tasks/silence.py` 纯判定（`decide`/`compute_last_activity`/`threshold_hours`，**防自激** `SELF_EXCITE_EVENT_KINDS` 排除 reminder_sent/escalated）；hub `run_silence_scan`——三态阈值提醒（Todo→创建者 / InProg→owner / InReview→频道人类，@Agent 视同 mention 唤醒）→ 升级（频道主流系统消息 + activity silence_escalation 逐人类）→ 升级后静默；判定/升级历史全在 task_events 纯推导无状态列。
- **F4 循环 Reminder**：`reminders/interval.py`（parse_interval/add_interval/next_after）；`create_reminder` 新门（recurring 必带 loop_contract 缺→422、once 携带→422、cadence 校验+一致）+ 同事务建 task_contracts 挂接行（kind=loop_contract, reminder_id, XOR task_id null）+ 回填 loop_contract_id；`run_reminder_scan` 按 kind 分支（once→done / recurring→塌缩重排保持 active）；daemon mcp create_reminder 扩 loop_contract 透传。
- **B-M4-1**：RemindersTab 强化（kind/cadence/next_fire/锚点/循环·契约角标/取消）+ `cancelReminder`/`useCancelReminder` + wsBridge `reminder.created/updated` case；Activity 置顶接真（结构就位，F3 silence_escalation 产出即渲染）。
- **`/code-review high`**（8 角度 × 25 agents → 10 CONFIRMED + 2 PLAUSIBLE）：**1 正确性 + 一簇 hub 清理/效率**全处理——① **recurring reminder 重放风暴**（旧实现每轮 +1 interval：建即触发 + 停机漏 K 周期后逐格重放洪泛）→ `interval.next_after` O(1) 塌缩到 >now 下一网格点 + 创建 recurring next_fire_at=now+interval；② 抽 hub `_post_system_message`（系统消息发射三处共用）；③ _silence_inputs 三 func.max → 单查询条件聚合；④ run_silence_scan channel N+1 → distinct IN 批取；⑤ run_reminder_scan 免回读 + `ledger.service.format_iso` 单源。PLAUSIBLE per-task SAVEPOINT **评估后不采纳**（与 tx.emit 提交后 flush 解耦产幽灵事件，单事务原子 + 下轮重试更安全）。
- **收口基线**：后端 **542 passed / 3 skipped**（483 → +59 M4a + review 回归，零回归）、web vitest **89**（76 → +13）、pyright 0、ruff 干净、`pnpm gen` 确定、双侧 build 绿。
- **实机 verify**：隔离临时库 + 真 uvicorn 8799 探针 **16/16**——沉默场景（todo/in_progress 双任务 reminder_sent → 任务 A escalated → @创建者/@owner/@人类目标正确 → Activity silence_escalation → 升级后静默）+ 循环 Reminder（recurring 无契约/once 携带 422 → recurring+loop_contract 201 + task_contracts 挂接行 + next_fire_at 塌缩重排）+ 3 浏览器截图（沉默流/Activity 置顶/Reminders 页签）+ console 0（[M4A-EVIDENCE.md](../verify/M4A-EVIDENCE.md)）。
- **块 M4a 收口**；接续 = 块 M4b。

## 11. M4b freshness 与 HeldDraft（块 M4b 收口 2026-07-10 = PRD M4 出口达成 = M4 里程碑完成，`1052ee6`）

- **编排**：Workflow 多 agent 并行——后端 **F5→F6 串行**（同改 server 文件保语义连贯）与前端 **B-M4-2** 并行两条流，各自测；集成守门（主循环）→ 实机 verify → `/code-review high` → 修复复跑。
- **F5 freshness 门 + 三键端点**：`guard/service.py` 判定单源（`compute_unread` scope=线程 id==root|thread_root==root / 主流 thread_root IS NULL、未读=id>read_position 且 author!=agent、无游标全量、空放行；`hold_or_update` 活动行再扣 held_count+1 靠 `begin_nested` SAVEPOINT 兜 IntegrityError；`_escalate` G5；`freshness_hold` 总装）。messages.py 门位 = 仅 agent、既有校验 + **幂等 hit** 之后、落库前；抽 `persist_message` 落库核心（post/release 共用保零行为差）。`routes/held_drafts.py` 四端点（GET 默认活动态 / release 原载荷不依赖 daemon + 跳过 freshness / discard 直投 503 回滚 / reevaluate 委托 hub）。hub `inject_guard_feedback`/`_held_reevaluation_combo`/`reevaluate_held`（**死锁规避**：路由 tx 只读、写 + daemon I/O 全在 hub loop 独立已提交 tx）。
- **F6 G4 定时 + GC 豁免**：hub `run_held_scan`+`_held_loop`（held_interval，**在线先探再翻 reevaluating**，提交后组合含 **deliver 推进 read_position** 防复扣死循环）；`gc.py` 活动 held file_ids 豁免。
- **B-M4-2**：HeldDraftCard（草稿折叠 / 未读跳转 / 本地读秒倒计时不推帧 / 三键仅人类 / 升级横条 / 终态回执 / 409 刷新 / 非-409 error toast）+ HeldDraftList 按 thread_root 归位 + ChannelChatScreen·ThreadPanel 挂载 + wsBridge `held_draft.*` + api/queries 三键 hook。
- **`/code-review high`**（8 角度 × ≤6 候选 → 对抗性 verify → **17 CONFIRMED + 2 PLAUSIBLE**）：5 正确性/健壮性修 + 1 簇清理 + 2 DRY 债挂账（明细 [M4B-EVIDENCE.md §5](../verify/M4B-EVIDENCE.md)）——**#4 幂等前置**（`ledger.lookup` 只读探账本，门前查 hit 回原 M1，absent 落库路径才 record；防重放遇新未读误扣→人类放行产生重复消息违 §1）/ **#5 reevaluate 终态守卫**（UPDATE 限活动态 + rowcount0→`HeldDraftResolved`→409；防并发复活丢弃草稿）/ **#6 G4 离线先探**（防离线行永卡 reevaluating、对账无 held 感知永不恢复）/ **#1 清单默认活动态**（`ACTIVE_STATUSES` 提公开单源 gc 复用；防活动 held 被终态挤到后页）/ **#3 三键 503 toast** / #7·#10 诊断走 helper 常量；#2 由 #6 覆盖；**#8·#9**（系统消息骨架/human_members 跨模块 DRY 债）挂账。
- **收口基线**：后端 **572 passed / 3 skipped**（542 → +30，含 review 新增 3）、web vitest **106**（89 → +17）、pyright 0、ruff 干净、`pnpm gen` 确定、双侧 build 绿。
- **实机 verify**：隔离临时库 + 真 uvicorn 8801 + **真 websockets daemon-sim** + 浏览器 WS 探针 **38/38**——held 场景（Pat 发消息被扣 202 → 卡片草稿全文+未读原因+guard.held → 放行 → **1 分钟内 message.deliver 送达 Rin**）+ discard 直投 + reevaluate（**deliver ack 推进游标→重发过门 201 死循环破除**）+ G4 超时自愈 + G5 连扣升级（escalated_at+系统消息@人类+held_escalation activity+停自动）+ playwright 3 截图（held 卡/升级横条/Activity 置顶）+ console 0（[M4B-EVIDENCE.md](../verify/M4B-EVIDENCE.md)）。
- **M4 里程碑收口**（§9a+§9b 出口清单全绿）；M4-HANDOFF 移入 archive/。**无待收口里程碑，接续 = M5+（未立项）**。

## 12. M5 第二 runtime·配置面与模板向导（M5 里程碑收口 2026-07-11 = PRD M5 出口达成）

- **块 M5a 第二 runtime 与配置面**（`da6833a`，详情 [M5A-EVIDENCE.md](../verify/M5A-EVIDENCE.md)）：Codex 适配器（`adapters/codex.py` CodexProcess 长驻 JSON-RPC、`_new_process` 按 runtime 分派、CODEX_HOME 隔离 + auth.json mtime 物化、probe_codex+skills）+ 每频道通知设置（GET/PUT + mode 门 mute 掐 mention activity + 原子 upsert）+ cron cadence（手写 5 段无依赖 + 塌缩重排 + DST fold）+ 技能白名单 UI（两 runtime 候选池）+ P12 阈值收编。实机 codex 0.144.0 PONG + REST 9/9 + code-review 5 CONFIRMED（cron 500/DST/通知 TOCTOU/codex 凭证/probe symlink）。契约 M5 全程零修订（H0 已落 A v1.0.6/B v1.3/E2 v1.0.1）。
- **块 M5b 模板与向导**（`b4203c4`→`12aaac6`→`42b7b64`→`bb760f0`→`bef88eb`，详情 [M5-EVIDENCE.md](../verify/M5-EVIDENCE.md)）：
  - **H5 模板域**：`serialize_canvas_to_body`（画布快照→TemplateBody：仅 task 节点/占位 owner 去重/plan_skeleton 带走/pos 不入/node key `n{idx}`）+ `validate_template_body` 单执法点（无环 detect_cycle + 引用一致）+ `upsert_builtin_templates` 启动幂等；**工程三角 builtin**（`templates/builtin.py` 6 节点线性 DAG + 4 角色占位 checker≠doer 话术 + briefing + 每节点 plan_skeleton）。
  - **H6 实例化事务器**（全仓首个 landing batch 消费者）：`routes/templates.py` POST instantiate——role_mapping 全覆盖 422/未知成员 422/无画布 404 **全前置于幂等 reserve-before**（record 先于副作用、req_hash 折 template_id、并发同键不重复落地批；reserve 后不再抛错=安全性所系，因 record 走 SAVEPOINT 写入未必随回滚撤销）；`instantiate_template` 单事务：落地批 kind=tmpl → 逐节点 create_node 全链（`tmpl:<batch_id>:<node_key>` 幂等 + `_layout_positions` 分层布局）→ 连边（无环兜底 + triplet SAVEPOINT）→ briefing @映射角色（唤醒）→ baseline bump → mark_done；重放 reconstruct 由 `ledger.batch_node_task_ids` 按 **seq 保序**派生；blocked-gating 天然生效。
  - **B-M5-2 前端**：SaveTemplateModal（占位提取表/gating disabled）+ TemplateWizard 三步（DAG 缩略图 / 同 runtime 互审 warning=`lib/templates.classifyRole` **仅按占位名判定** / 预览 / 实例化跳画布）+ CanvasTab 模板钮 + SetupChecklist 003 接真 + 幂等键每次提交作废重置 + `crypto.randomUUID` 兜底。
- **并行审计（4 agent 前置于实机）**：**1 blocking**（实例化幂等 reserve-before：原 record 在副作用后 → 并发同键各建一批）+ **1 major**（向导「新建 Agent」死控件 + awaitingCreateFor 误映射）+ minor 全修（未知成员 422/validate 再执法/briefing 空跳过/前端幂等键）+ 覆盖补齐（SetupChecklist 003/向导预览）。
- **H7 实机 verify = PRD M5 出口**：隔离库 + 真 codex 0.144.0 + 真 uvicorn + playwright 1440×900——codex 真机 PONG；**e2e 12/12**：工程三角实例化（实现=Codex Hank/评审=Claude Rin）→ 落地批 done/6 节点 5 边/TaskPlan 初稿/briefing @codex+claude/gating 初态 → 幂等重放 → **全管道逐节点 claim/T7 handoff/in_review/done 走到人类终审 done（gating 逐级解锁）**；5 前端截图（菜单/向导三步/画布）+ console 0。
- **verify-surfaced 修复（单测漏网实机现形）**：`classifyRole` 原并入 description → builtin「实现工程师」含「交独立验收」被误归 review 致 FR-7.3 warning 失效 → 改仅按占位名；实例化节点原全落原点堆叠 → `_layout_positions` 分层。
- **`/code-review high`**（8 维度 workflow → 36 候选→27 kept→**6 distinct CONFIRMED 全修**，明细 [M5-EVIDENCE.md §5b](../verify/M5-EVIDENCE.md)）：**B** 幂等身份漏 template_id（跨模板同键回放错模板）→ req_hash 折 template_id / **C** 重放顺序漂移（(created_at,op_id) 字典序）→ 按 seq 保序 / **A** 前端幂等键不重置 → pickTemplate·onRoleChange 作废 / **D** randomUUID 非 secure context 崩 → 兜底 / **E** 「ApiError 不回滚事务」误述（实证 get_tx 对 ApiError 确回滚普通写，op_id 残留实因 record SAVEPOINT）→ 注释改机制无关 / **H** 死参。PLAUSIBLE 挂账（briefing 全 @/layout 重复/serialize N+1/fail-closed M6-only）。
- **收口基线**：后端 **712 passed / 4 skipped**（672 → +40）、web vitest **175**（142 → +33）、pyright 0、ruff 干净、`pnpm gen` 确定、双侧 build 绿。
- **M5 里程碑收口**（§9a+§9b 出口清单全绿）；M5-HANDOFF 移入 archive/。**M1–M5 全收口，无待收口里程碑，接续 = M6（未立项）**。

## 13. M6a Project 与交付链（M6a 全收口 2026-07-12：实现 + 实机 verify 20/20 + code-review 10 findings 全修）

- **契约与补遗**：M6 立项落 A v1.0.7/B v1.4/D v1.0.3；owner 随实现批准三组连带补遗并同步两文档 header/变更记录：A **v1.0.8**（`worktrees.merge_commit`、`ProjectPublic.channel_ids`）+ B **v1.4.1**（Project 精确请求/响应）；B **v1.4.2**（COMPUTER_HAS_PROJECTS，错误码 29，Agent→Project 固定删除门序）；A **v1.0.9** + B **v1.4.3**（TemplateNode 保存/实例化贯通 `writes_code/project_id`）。C v1.0、E v1.4、E2 v1.0.1 零修订。
- **波 1 地基**（`d564ebf`）：J3-cal 用脚本生成 scratch git repo 完成 win32 worktree/merge/diff/冲突/编码/锁与占用探针 **10/10**，结论归 `scratchpad/GIT-CALIBRATION.md`；J0 contracts/mock/conformance/gen 同步；J1 `0008_m6a` 一次建 projects/channel_projects/worktrees（含 merge_commit）并给 tasks 加两列，从零与 M5 增量升级双路绿。守门 **724/4 skipped + web 175**。
- **波 2 执行域**（`62939f2`）：J2 Project CRUD/频道绑定/`channel_ids`/repo 校验/Computer 引用门与频道级联；J3 writes_code 激活派生 worktree、ensure/cleanup 幂等、状态回流、对账 #5、keep_days 清理与绝对路径消息注入；模板补遗经 create_node 链原样落任务行，目标频道绑定在副作用前复核。守门 **772/4 skipped + web 175**，独立审计无 High/Medium。
- **波 3 交付面**（本提交见 HEAD）：J4 daemon `git.diff` + REST 代理 + TaskDetail.worktree，覆盖增删改/重命名/二进制/三级截断/cleaned/404/503/超时；J5 check/merge 自动触发、仅 failed retry、DAG 序 `merge --no-ff`、成功持久 `merge_commit`、冲突任务派回、取消/超时恢复与保留期收敛；J6 review_verdict 四值、needs_human @人类、builtin 话术与中立消息查询；B-M6-1 完成 Project 设置、Diff 卡、系统节点/Retry、verdict、冲突卡及 worktree WS 更新。
- **审计与界面证据**：并行审计发现的两项 Medium 已闭合：daemon JSONL 改同目录临时文件 `flush+fsync+os.replace`，撕裂/replace 失败可恢复；同物理树 alias cleaned 会在 report/converge 两路径广播 fresh rows，重复 cleaned 不重复 alias。复核后无剩余 High/Medium。B-M6-1 在 1440×900/390×844 屏对照无横向溢出、console 0，三张 `docs/verify/m6a-*.png` **仅是 UI 对照，不是 M6a 真机证据**。
- **波 3 守门**：后端 **813 passed / 4 skipped**，web **194**，pyright 0、双 tsc、ruff、`pnpm gen` 后零 unstaged diff、web build 全绿。M6-HANDOFF §9a #1–#10/#12 已勾。
- **实机 verify（`bc70cd5`）**：真 uvicorn + 真 websockets daemon-sim（真 `git.py`）+ 真 scratch 仓库端到端 **20/20 ALL PASS**（场景 A 交付链双任务→worktree 交付→merge --no-ff→check 绿→Diff；场景 B 冲突派回→解决→retry 合并成功），证据归 `docs/verify/M6A-EVIDENCE.md` + 4 截图；`/code-review high` 8 角度 workflow → 10 findings（1 REFUTED）登记待修。
- **code-review 修复收口（T1 终段，Codex 起草 + Fable 5 对抗审查/收口）**：owner 拍板 #1 后台化（Design C 保序）/#7 保连续前缀语义删死代码/其余全修。10 条全落地：daemon worktree 指令后台通道（单车道、ack 仍 op 后发、断连不取消仅 shutdown 取消）、ensure 失败诊断归属+DIAGNOSTIC_APPENDED+累计 3 次一次性升级喊人、reconnect 握手复验 active 行（`revalidation_plans`，conflicted 排除、周期不复验）、冲突派回幂等（未终态同树复用/二次真冲突建新）、`GitQueryError`→422 透传 git prose、`CardKind.MERGE_CONFLICT` 结构化冲突卡全链、diff 单进程切分（fail-closed 守卫）、菱形 merge alias 广播去重（进展消息 per-node）、#10 注释。**#3 复验帧序外溢修 5 个 server 测试**（`drain_revalidation` 桩辅助）+ **新增回归 14 项**；坑：升级系统消息本身经 MESSAGE_CREATED→`_deliver_message` 低延迟扫描会再发一次 ensure，测试须消费。守门新基线 **827/4 skipped + web 195 + pyright 0**；m6a_verify 复验 20/20（4 轮 3 净 1 环境性 REST 超时，probe 既有 DB 锁重试脚手架佐证环境噪声）+ probe 收尾 `suppress(BaseException)` 修假 traceback。M6b 的 orchestration/proposals/0009 未触碰。

## 14. M6b Orchestrator 拆解链（M6 里程碑收口 2026-07-12 = PRD M6 出口达成，`d303475`）

COLLAB-MODEL v2「Fable 单窗编排」：执行/审计/评审派 Opus 子代理，关键关口（J9 硬关口/J12 verify/话术定稿/正确性关键修复）Fable 亲做，code-review 评审面全 Opus、Fable 终裁。

- **契约**：A **v1.0.10**（0009 批次 = proposals + agent_role_templates 两表 + agents.role_template_key 列，owner 2026-07-12 拍板方案 A）。B/C/D/E/E2 零修订。
- **波 1 内核**（`95d190c`，864/261）：J7 同构校验内核（`kernel/decomposition.py` V1–V14 + `<control>` 解析 + 指纹，`lib/decomposition.ts` 镜像，`golden/decomposition.json` 双跑逐字节）；J11 骨架（模板治理 PATCH/DELETE builtin 409 + 角色模板数据）。主循环追修 J7 两缺口（严格度补齐 ≥TaskPlanBody/码点长度）。
- **波 2 提案域**（`3a78799`，895/261）：J8 = 0009 迁移（proposals 部分唯一索引 + agent_role_templates + agents 反射式加列）+ 8 态状态机（`PROPOSAL_TRANSITIONS` 单点）+ 三入口归一（decompose REST/T1/线程 `<control>`）+ 上下文注入（S1 直投）+ 修复循环（每 rev 2 轮，第三败升级 @人类）+ Superseded/rev+1 + 对账 #6 + 24h 提醒纯推导。
- **波 3 确认落地**（`832f2dc`，915/296）：**J9 硬关口**（Fable 亲自逐不变量对抗审查 + 重写 2 blocking）——架构 = 202 异步增量落地：confirm 短事务（CAS→apply_adjustments 六 op→权威重验→落账→建批）→ hub 执行器**步进原子**（每步 = 节点 + 其全部入边一 gateway_tx，封「裸系统节点空成功」窗口）；`_transition`/confirm/reject 条件 UPDATE（pysqlite 读自动提交≠串行化点，防双确认双批）；merge 自动追加 deps=writes_code 前沿；fail-closed 持久性（独立连接 persist）；A5 亲跑。B-M6-2 前半（拆解入口/提案卡/wsBridge）。
- **波 4 增量**（`3d3e12f`，936/348）：J10 delta（classify 入口/`orchestration/delta.py` 五步校验含信封+过滤复用 kernel/confirm 部分接受 removed_ops/F9 base 过期 409+failed/落地共享步进 runner remove_edge→remove_node[执行期 NODE_ACTIVE 复核]→add_node+入边→add_edge/O9 四端点 Agent 403）∥ B-M6-2 后半（草稿层 overlay+确认条防呆/delta 面板逐 op 剔除/rev 替换/P12 编排组/LandingToaster）。双 Opus 子代理并行、Fable 逐文件过目集成。
- **阶段 4 并行审计**（`19fcfb5`，943/349）：5 维 Opus finder → Fable 逐条终裁修 7（1 blocking：落地期系统节点认领抑制 `_channel_landing_in_progress`——封 delta remove 先序重开的空成功窗口；SM-F1 `_transition` 全面 CAS 化 + classify 竞败降级 + supersede 遇 landing 跳过 + initiate 复用现行提案；SM-F2 并发建案 SAVEPOINT；门 F1/F5 patch_node/layout；门 F2 delta 落地消息 @激活 owner；镜像 F1 DeltaPanel running 系统节点 + 面板互斥）+ J11 话术定稿（第 8 条 delta 通道，与 DELTA_BASE_MISMATCH hint 互为兑现）。
- **J12 实机 verify**（`818a483`）= **PRD M6 出口 48/48 ALL PASS**（Fable 亲跑，真 uvicorn+真 websockets daemon-sim(真 git.py)+真 scratch 仓库，REST 扮演 Orch/工人全链走生产码）：S1 拆解全链（A1/A3/A4）/S2 冲突派回（A6）/S3 修复循环（A2）/S4 A5 崩溃重放（落地中 kill→续跑 10 节点无重复无缺失+已落地恰一条）/S5 delta+O9（部分接受/F9）/S6 single_task（A7）/S7 直落（A8）——拆解设计 A1–A8 逐条勾销；真 git log 佐证双 --no-ff merge commit + 冲突解决 retry；证据 = `docs/verify/M6-EVIDENCE.md` + `M6-VERIFY-results.json` + 2 截图。
- **code-review high 收口**（`d303475`，947/354）：8 维 Opus finder → 对抗核实（默认证伪）→ Fable 终裁 13 findings（12 CONFIRMED/1 REFUTED），修 8——3 major（delta 同 rev base_hash 对称刷新 / 模板 instantiate 补 O9 人类门 / kernel 5 处 unhashable 枚举 `_is_str` 守卫，与 TS 镜像双跑一致）+ 4 minor（remove_node TOCTOU 锁内重验 / fail-closed 不重扫系统节点 / ProposalCard delta 指标 / 短路效率）+ golden +4 判例。3 观察挂账（N+1/索引谓词双源/downgrade FK）登记 M6-HANDOFF §8。
- **守门终态**：后端 **947 passed / 4 skipped**、web vitest **354**、pyright 0 + 双 tsc、ruff 干净、`pnpm gen` 确定（golden 54 双跑）、web build 绿。关键教训（记忆正文）：pysqlite 读自动提交非串行化点→凡状态机边写必条件 UPDATE（J9/SM-F1 反复印证）；delta 先删后加的步序在 fail-closed 部分提交前缀上会重开系统节点空成功窗口→落地期抑制须覆盖 fail_closed（不重扫）；内核枚举成员测试须 `_is_str` 守卫防 unhashable 崩溃并与 TS 镜像对齐。

## 15. M7 预览、部署与打磨（M7 里程碑收口 2026-07-13 = PRD M7 出口达成 = **MVP 全里程碑 M1–M7 完成**，`b18adbe`）

COLLAB-MODEL v2「Fable 单窗编排」续用：执行/评审派 Opus 子代理，关键关口（K2-cal 校准/K9 实机 verify/幂等·对账正确性修复/code-review 终裁）Fable 亲做。两块竖切（M7a 预览链先行 / M7b 部署+成本+收尾），块内分波并行。

- **契约**：A **v1.0.11**（fail_log_tail/两处部分唯一索引/排队措辞对齐）· B **v1.5**（§13 预览部署条文+§13.4 成本口径）· C **v1.0**（连续零修订核对）· D **v1.0.5**（对账 #9/#10 + PORT 注入 + preview.status log_tail + jitter-survive hello boot_nonce/previews 快照）· E **v1.5**（新增 trigger_deploy 工具，零工具连胜止于 M6 四届）· E2 v1.0.1 零修订。**错误码零新增（仍 29）**。
- **块 M7a 预览链**（`82ebd1b`）：**K2-cal win32 长驻 dev server 实测校准**（Fable 亲跑 5/5，最关键坑 = win32 SO_REUSEADDR 同端口双绑不被 OS 拒 → daemon 进程内注册表自持端口唯一性）→ 波1 K0 契约登记（ENDPOINTS_M7 7 端点/四模型/trigger_deploy）∥ K1 0010 迁移（preview_sessions + 单活跃部分唯一索引，谓词单源）→ 波2 K2 daemon PreviewRunner（PORT 注入/健康-存活并行竞速/杀树无孤儿）∥ B-M7-1 前端预览面板 → 波3 K3 server 预览域（ensure+touch/回收三触发/对账 #9，Fable 亲修 pre-commit 下发竞态→after_commit 硬保证）→ **实机 verify 14/14**（真 dev server + iframe HTTP 200 + idle 回收）→ code-review 7 CONFIRMED 全修 → jitter-survive 增补（存活预览 survive WS jitter，真重启才 fail-close）。
- **块 M7b 部署·成本·收尾**（`8d26abe`→`b18adbe`）：
  - **K4 部署域全链**（`8d26abe`）：0011 迁移（deployments 单一非终态部分唯一索引，谓词单源 `_DEPLOYMENT_ACTIVE_WHERE`）；routes/deployments.py（POST R8 无角色门/422 无 deploy_command/503/409 DEPLOY_IN_PROGRESS 索引兜底+SAVEPOINT/Idempotency-Key/主干 HEAD 快照/token_summary 新账纯 SQL 落列/tx.after_commit 提交后下发）+ GET + GET log（server 直读落盘）；hub deploy.log（chunk_seq 去重+落盘+queued→running CAS）·deploy.finished（终态 CAS+结果卡多绑定频道+诊断）·**对账 #10（真重启 running→fail-closed 不重跑@触发者 / queued 安全重发，铁律 3 副作用不可重放）**；daemon deploy.py（DeployRunner 流式日志+末 URL 提取+30min 超时杀树+deployment_id 幂等）+buffer 双缓冲。
  - **波2**（`e06a793`）：K5 trigger_deploy 工具（mcp.py +1 纯代理/结构化透传/E2 codex 零改动/X-Acting-Member 留痕）∥ K6 GET /usage 三层 GROUP BY（task 恒{0/1,1}/agent·canvas 任务集/永无货币/聚合 SQL 单点）∥ B-M7-2 部署卡+成本面（确认弹窗/日志跟随+胶囊/token 小结 Σ+覆盖率/画布 UsageChip/wsUplink 订阅制+重连 resend）。
  - **波3**（`9082827`）：K7 性能小批四件（landing._post_landed_message / hub._report_usage / templates._plan_skeleton / search——**语义逐字节等价** + 查询数断言 O(1)/O(批)）∥ K8 预留位审查文档 `docs/M7-RESERVATION-AUDIT.md`（三信任基座盘点 + R-1~R-15 挂账登记，零代码）。
  - **K9 实机 verify**（`f6655d1`）= **PRD M7 出口 29/29 ALL PASS**（Fable 亲跑，真 uvicorn+真 daemon-sim[真 PreviewRunner/真 DeployRunner 起真子进程]+真 scratch git）：交付→预览验收 iframe HTTP 200→合并 --no-ff→部署人类+Agent 双通道(末 URL 提取/GET log 落盘/新账小结/结果卡多频道)→409 不排队→GET /usage 三层→对账 #10 真重启 fail-closed(exit_code=NULL)@触发者→对账 #9 预览 fail-close；+ 浏览器可视 E2E（真前端渲染部署卡 URL·退出码·耗时·新账 Σ1.6k·上报 1/1，Agent 卡 Σ0 新账正确）；证据 = `docs/verify/M7-EVIDENCE.md`+`M7B-VERIFY-results.json`+`M7B-BROWSER-E2E.txt`。
  - **code-review high 收口**（`b18adbe`）：8 维 Opus finder→1 票对抗核实（40 候选→39 去重→12 CONFIRMED），Fable 终裁修 **4 真缺陷**（① GET log next_after 无分页却返 total→空按钮→改 None ② `_report_deploy_log` 去重游标先于提交推进+事务内落盘→回滚吞帧/重复→**落盘+游标挪到提交后** ③ usage level=agent usage 按 agent_member_id 聚合而覆盖率按 owner 任务集→不一致→统一 owner 任务集 usage==Σbreakdown ④ 诊断去重抽 `_emit_agent_diagnostic` 单点）+ 其余 CONFIRMED 登记 R-10~R-15（契约授权/MVP 安全残窗，M8+）。
- **守门终态**：后端 **1071 passed / 4 skipped**（M7a 起点 955）、web vitest **403**、pyright 0 + 双 tsc、ruff 干净、`pnpm gen` 确定（golden 58 双跑）、web build 绿、**K9 实机 29/29**。关键教训（记忆正文）：win32 SO_REUSEADDR 双绑不被 OS 拒→长驻子进程端口唯一性须 daemon 进程内自持；部署副作用不可重放→running 崩溃 fail-closed 不重跑（对账 #10 与落地 #4 可重放性质相反）；遥测去重游标（内存）+ 非事务落盘须**挪到 tx 提交后**，否则回滚吞帧或重复落盘（收口复审印证）；读面聚合的 usage 与覆盖率/明细须同源任务集，否则口径互不一致。

## 16. M8 实用化加固与编排质量（块 M8a + 块 M8b 收口 2026-07-14；块 M8c 收口见 §17）

MVP 后首个非 PRD 里程碑（任务书 [M8-HANDOFF.md](M8-HANDOFF.md)，owner 两轮拍板）：三块竖切——**M8a 加固批**（并发正确性地基）/ **M8b O8 编排质量线**（编排语义：拆得拢、拆得好）/ **M8c 外壳与收官**（产品面 + 教程）。协作模式：Opus/Fable 单窗编排，风险面（读循环并发、内核三处同步、CAS 正确性、守门）主循环亲做，独立前端件派子代理。

- **块 M8a 加固批收口**（`ab84406`）：L0 契约落笔（A **v1.0.12** summary_runs + canvas_nodes.upstream_policy / B **v1.5.1** NodeCreate.upstream_node_ids + replan 403 rule=O8）+ 迁移 0012 前移 → L1 手动系统节点原子建边（POST /nodes 消费 upstream_node_ids 同 tx 建节点+入边，封 K1「裸系统节点空成功」竞态）→ **L4 读循环并发地基**（CR-M8-1 同族收尾：`_reader` 只收帧入队 + 独立 `_writer` 消费、DB 写 offload `to_thread` 不阻塞 loop/不撞锁撕连接；held discard inject 挪 after_commit；铁律 4 扩「跨进程同步等待不得跨持锁事务、读循环不持锁写」）→ L6 部署日志残窗（R-10 游标 sidecar 持久化 / R-14 前端 chunk_seq 拼接去重）+ B-M8-1 前端五件。守门 pytest 1089/4·vitest 463。
- **块 M8b O8 编排质量线收口**（后端 `cb5d00b`/`7d4c910`+`e68abcd`/`872762a`；前端 B-M8-2 `a51637c`）= [Orchestrator汇总设计.md](../../../orchestrator_docs/Orchestrator汇总设计.md) v1.0.1 全量兑现：
  - **L7 W9 双档 satisfied 内核**（`cb5d00b`，纪律 8 首改 graph 组）：`derive_blocked` 扩 done_satisfied/terminal_satisfied 双集合 + 节点 policy 映射（签名向后兼容 3 参 = 纯 strict 回归），py 权威 + `lib/graph.ts` 镜像 + golden 扩 5 partial 判例三处同步双跑逐字节；汇总节点落地默认 `upstream_policy=partial`；patch_node 人类改档、Agent 403 rule=O9。**实施补形**：`NodePatch` 漏 upstream_policy 改档字段 → 纪律 1 升 **B v1.5.1→v1.5.2** + 设计 v1.0→v1.0.1（正文 §5.2/裁决 #6 既定，契约影响表漏列）。
  - **L8 汇总执行域**（`7d4c910`+`e68abcd`，新模块 `orchestration/summary.py`）：`collect_summary_inputs` 有界摘要（≤12 节点/≤5 deliverables/≤160 截断/≤8KB，未覆盖清单 = partial 放行代价）+ `summary_fingerprint`（复用 fingerprint 内核）+ **summary_runs CAS**（ensure lazy / advance_progress 进展轮 fp 变才计+幂等 / note_wakeup 空转轮 stall++ / add_repeat_stall / consume_replan 预算 CAS / recover / _set_blocked CAS / post_coordination_block 单点）。轮语义双入口单点化（hub scan 结构进展轮 / delivery 空转 stall 轮，防返工锚点 4）；gating 阻断双面抑制自动唤醒；恢复（人类线程发言 / force-start 同事务清 blocked_at 归零，replan_used 不重置）；replan 第 2 次 403 rule=O8。F6 崩溃续算靠持久 summary_runs + 原子 CASE UPDATE 内在保证。
  - **L9 质量回路 + 话术**（`872762a`）：新模块 `orchestration/quality.py`（`adjustment_signal_body` 单源信号体，landed_hash≠proposal_hash 或 adjustments 非空 → 结构化，未调整 → None 防噪声）；带调整落地 → source 线程质量信号系统消息 @proposer（durable 留痕）+ hub LANDING_COMPLETED 提交后 GUARD_FEEDBACK 直投；**REJECTED 按 M6b 教训仅被动留痕不主动直投**（主动唤醒诱发未请求重提）；role_templates builtin +2 节（汇总职责 / 质量信号）upsert 幂等。
  - **B-M8-2 O8 前端可见面**（`a51637c`，纯前端 + 1 处 wsBridge，零后端/零契约/零迁移）：单源解析 `lib/summary.ts`（后端护栏消息体字面量契约的唯一前端镜像，逐字节测试守门）；① 汇总任务线程横幅 `SummaryBanner`（态由 `deriveO8Banner` 从线程系统消息体派生——**零新端点**，护栏可见哲学延伸到前端；active 轮数 N/8+未覆盖 / blocked 原因+计数+force-start 恢复[复用 ForceStartModal，同事务 recover]）② 画布汇总/partial 节点 badge ③ `NodeInspector` upstream_policy strict↔partial 改档（patch_node，canvas.node_updated 反流刷新）④ `SummaryCard` 摘要系统消息卡片化。**wsBridge 补**：threaded `message.created` 失效 `qk.thread`（qk.thread 原无 WS 失效——O8 系统消息落线程后横幅/卡片靠此实时刷新，顺带收敛线程回复实时性）。
- **守门终态**：后端 **1117 passed / 4 skipped**（M8a 起点 1075）、web vitest **502**（B-M8-2 起点 472）、pyright 0 + 双 tsc、ruff 净、`pnpm gen` 确定（L7 后逐字节稳定，golden 含 partial 判例）、web build 绿、工作树干净。**块 M8b 出口 §9b #7~#10/#12 全绿；#11「O8 真机全链」按任务书「可并入 L13」延至块 M8c**（daemon-sim 可控多轮唤醒场景）。关键教训（记忆正文）：判定归 server/控制归代码（输入有界·循环有顶·放行有策略）；护栏可见哲学从系统消息延伸到前端横幅（零暗信道·零新端点·人机同源）；纪律 8 首触 graph 组语义必"新增档不动旧档"（strict 默认让全量既有 golden 逐字节不变）；O8 阻断/恢复全 CAS 条件 UPDATE（并发唤醒恰一次计数）；qk.thread 原无 WS 失效是既有潜伏面，消费线程系统消息的横幅须补 threaded message.created → qk.thread 失效。**接续 = 块 M8c**（B-M8-3 外壳 + L10/L11/L12/L13 = O8 全链真机 verify + 教程收官）。

## 17. M8c 外壳与收官（M8 里程碑收口 2026-07-14 = M8 全部三块完成，`1638b34`）

块 M8c「产品面 + 教程」收口 = M8 里程碑完成。波序 c-1→c-4，Opus 单窗编排（风险面 L11/L13 主循环亲做，独立前端 B-M8-3 派 sonnet 子代理，主循环 review diff + 事实核实）。

- **B-M8-3 产品化外壳 + L10（`9372090`，纯前端 + L10 零新端点）**：POST /channels 后端既完备（L10 预判成立）。① Members 页「创建 Agent」按钮（`.ms-head` h1 flex:1 右顶）→ CreateAgentModal ② SetupChecklist 步骤 002 `onAction` → CreateAgentModal（既有 ToastProvider 内）③ 侧栏「新建频道」死壳 → `NewChannelModal`（name/desc/private，NAME_TAKEN 就地报错）+ `api.createChannel` + `useCreateChannel`（镜像 useCreateDm，invalidate channels）。
- **L11 新 Agent 入职问候（`9372090`，PRD FR-1.4；裁决 #9 默认关）**：复用既有 `workspace.onboarding_greeting` 死壳开关（原无消费者，WorkspaceSettingsModal 已在用）。**默认关一处改 = `models.py` server_default `text("0")`**——0001 迁移由 `Base.metadata.create_all` 从 **live metadata** 建表，故改模型 server_default 即改所有新建库 DDL 默认（+seed.json false + 契约 entities 默认 False + gen rest.ts `@default false`；conformance 只查列名集，无迁移风险）。**触发点** = lifecycle START 成功后（result≠failed）`_maybe_onboarding_greet`：双门 = 工作区开关 + diagnostic 幂等标记 `agent.onboarding_greeting`（本地专用类型，同 `_DIAG_REMINDER_CANCELLED` 体例，DIAGNOSTIC_TYPES 开放集）→ 标记提交前写 + `tx.after_commit` best-effort 直投 `hub.inject_onboarding_greeting`（InjectKind.SYSTEM，离线静默，铁律 4 跨进程等 ack 不跨持锁事务）。**重启不重复**靠标记 airtight。
- **L12 教程收官 + L13 m8_verify（`1638b34`）**：L12 = `使用教程/README.md` §13 M8 新增（新建频道 / 入职问候 / O8 汇总横幅·partial / 质量回流）+ 建 Agent 通用入口 + 速查表；delta 审查面 UX 复盘（B-M8-1 ③ 遮挡修后无残留，保持现形）。**L13 = `scratchpad/m8_verify.py` 10/10 ALL PASS**（真 uvicorn + 真 daemon-sim[FakeAdapter]：S1/S2 外壳端点 / L11-0..4 问候线级[FakeAdapter.injects 观测默认关·开→SYSTEM inject 一条·标记一条·重启不重复] / L1-1 原子建边非空成功 / L1-2 悬空 422）+ **浏览器可视 E2E**（Members 创建 Agent + 侧栏新建频道弹窗 DOM 核实、shell 建的 m8-shell/ShellBot 反流 UI、零 console 错误；截图工具本机超时用 get_page_text/javascript_tool，同 B-M8-2）。证据 = [M8-EVIDENCE.md](../verify/M8-EVIDENCE.md)。**O8 护栏正确性归单元套**（1122 passing 跑真 ORM/DB；防返工锚点 7 不烧真 CLI 多轮空转）。
- **c-4 /code-review high**：8 角 finder → 5 findings **全低危 0 修**（终裁登记挂账/观察项）：**F1** 入职问候 TOCTOU 双问候（并发双 START，diagnostic_events 无 (agent,type) 唯一约束；效应良性=重复问候，单用户双击才触发）→ 观察项；**F2** server_default 1→0 不改既有库行，既有 dev 工作区保留 greeting-on（违裁决 #9 于既有数据，新装已兑现）→ 观察项/文档；F3 无标记 Agent 下次 START 均问候（标记保一次性）；F4 setup 步骤 002 建 Agent 不推进 setup_state（既有，setup 非门）；F5 ChannelList onCreated 选中尚未进快照频道（自愈，同 useCreateDm 型）。
- **守门终态**：pytest **1122 passed / 4 skipped**（c-1 起点 1117）· web vitest **512**（B-M8-3 起点 502）· pyright 0 + 双 tsc · ruff 净 · `pnpm gen` 确定 · web build 绿 · 工作树干净。**关键教训**：① 「默认关」若靠模型 server_default，须知晓 0001=metadata.create_all 从 live model 建表（改模型即改新库 DDL，但**不改既有库行**——既有数据默认需另迁或文档，F2）；② 一次性幂等若无唯一约束 = read-then-act TOCTOU（F1，与 CAS 纪律同族——单用户良性故挂账，多用户化前收）；③ 复用死壳开关（onboarding_greeting）比新建字段省一次契约/迁移；④ 本机截图工具超时是既有环境事实，DOM 核实（get_page_text/javascript_tool）是可靠替代。**M8 里程碑完成，接续 = M9 未立项**。

## 已失效结论

| 历史表述 | 当前结论 |
|---|---|
| 项目只有文档与设计稿，没有产品代码 | M1/M2 产品实现、hardening 与二轮 review 修复批已完成 |
| 下一步从 A1 或 M1 实现开工 | M1/M2 全部收口，当前任务书 = M3-HANDOFF |
| M1/M2 契约或实现任务书是当前唯一入口 | 当前入口为 `CURRENT-HANDOFF.md`；开工入口 = `M3-HANDOFF.md` |
| `238`/`340`/`384`/`387 passed` 是最新测试基线 | 最新基线为 `421 passed, 3 skipped` + vitest 23（块 M3a 收口） |
| 前端仅依赖手工切换 API 基址或 mock `8642` | 已完成同源与代理路径 hardening，具体见修复报告 |
| pyright 有 109 个既有错误挂账 | 挂账批3 已清零（实清 133），pyright 已并入 `pnpm typecheck` 守门 |
| `421 passed` + vitest 23 是最新基线 | 挂账三批后为 `428 passed, 3 skipped` + vitest 23 |
| `428 passed` + vitest 23 是最新基线 | **M3b 收口后为 `483 passed, 3 skipped` + vitest 76**（M3 里程碑完成） |
| 接续 = 块 M3b 画布与 gating | M3 里程碑已收口；接续 = M4（已收口，§10+§11） |
| `483`/`572`/`672 passed` 是最新基线 | **M5 里程碑收口后为 `712 passed, 4 skipped` + vitest 175** |
| 接续 = M5（模板与向导）/ M5b 待开工 | **M5 里程碑已收口（§12 = PRD M5 出口达成，`bef88eb`）；M6 已立项，M6a 实现波次完成并停在真机 verify 前（§13）** |
| `712`/`772 passed` + vitest `175` 是最新基线，或 M6a 波 2 待提交 | **M6a 波 3 实现守门后为 `813 passed, 4 skipped` + vitest `194`；波 2 已提交 `62939f2`，当前停在 M6a 实机 verify 前** |
| `813`/`827`/`915`/`936`/`943 passed` 是最新基线，或 M6b/M6 未收口 | **M6 里程碑收口后为 `947 passed, 4 skipped` + vitest `354`（§14 = PRD M6 出口达成，`d303475`）** |
| 接续 = M6b（M6a 全收口；M6b 未开工）/ M6b 波 X 待提交 | M6 里程碑已整体收口（§14）；M7 已收口（§15） |
| `947 passed` + vitest `354` 是最新基线，或 M7 未收口 | **M7 里程碑收口后为 `1071 passed, 4 skipped` + vitest `403`（§15 = PRD M7 出口达成，`b18adbe`）** |
| `1071 passed` + vitest `403` 是最新基线，或 M8/M8b 未收口 | **块 M8b 收口后为 `1117 passed, 4 skipped` + vitest `502`（§16；M8a `ab84406` / M8b L7 `cb5d00b`·L8 `7d4c910`+`e68abcd`·L9 `872762a`·B-M8-2 `a51637c`）** |
| MVP 全里程碑 M1–M7 完成，无后续 PRD 里程碑（M8+ 未立项） | **M8 已立项，块 M8a+M8b 已收口（§16）** |
| `1117 passed` + vitest `502` 是最新基线，或 M8c/M8 未收口 | **M8 里程碑收口（全部三块）后为 `1122 passed, 4 skipped` + vitest `512`（§17 = M8c 外壳与收官，`1638b34`）；接续 = M9 未立项** |

## 当前接续任务

1. **M8 里程碑收口（2026-07-14）= 全部三块完成，接续 = M9（未立项）**：块 M8a（`ab84406`）+ 块 M8b（`4cc0f4d`）+ **块 M8c（`9372090`+`1638b34`，外壳/L11 入职问候/L12 教程/L13 实机 10/10/code-review 5 全低危 0 修，见 §17）**。守门 **1122/4 skipped + web vitest 512 + pyright 0 + ruff 净 + gen 确定 + build 绿**；实机 [M8-EVIDENCE.md](../verify/M8-EVIDENCE.md) 10/10 + 浏览器 E2E。契约 A **v1.0.12**/B **v1.5.2**/C v1.0/D v1.0.5/E v1.5/E2 v1.0.1（错误码仍 29；M8c 零契约新增，仅 entities WorkspacePublic.onboarding_greeting 默认 True→False）。M8c 代码复审 F1/F2 挂账见 CURRENT-HANDOFF §5。**M9 候选**：多用户/登录/邀请（auth 底座）· 多租户/多机/多副本（R-1~R-9/R-11/R-12/R-15）· 部署适配器专用集成。恢复入口先读 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)。历史 MVP M1–M7 收口见下 #2/§15。
2. **M7 提交链**：M7a `6eb78ff`(波1 K0+K1+K2-cal)→`9cf4318`(波2 K2+B-M7-1)→`3812f06`(波3 K3)→`9a2b17c`(verify 14/14)→`82ebd1b`(code-review)→`d238988`(jitter-survive)；M7b `8d26abe`(K4 部署域)→`e06a793`(波2 K5∥K6∥B-M7-2)→`9082827`(波3 K7∥K8)→`f6655d1`(K9 verify 29/29)→`b18adbe`(code-review 收口 = MVP 完成)。细节见 §15。
3. **M6 提交链**：M6a `d564ebf`→`62939f2`→`6f6fc93`→`bc70cd5`→`404aaa8`；M6b `95d190c`(波1)→`3a78799`(波2)→`832f2dc`(波3)→`3d3e12f`(波4)→`19fcfb5`(阶段4审计+J11)→`818a483`(J12 verify)→`d303475`(code-review 收口)。历史 M6a 细节见 §13。
3. **M5 里程碑已整体收口（§12 = PRD M5 出口达成，`bef88eb`，实机 e2e 12/12 + codex PONG + 5 截图 + 并行审计 1 blocking+1 major 全修 + code-review 6 CONFIRMED 全修）**。M5-HANDOFF 已移 archive/。
4. ~~`_emit_activity` 迁 service 层~~ **已收（M4a F2）**：迁 `activity/service.py`（conn 注入式，hub 后台可调、提交后广播）。~~`patch_task` 清空 silence_override_h~~ 亦已收（同 F2，白名单式 null 清除）。
5. 独立性能小批（不阻塞）：hub `usage.batch` 逐事件 SELECT 可批内 IN 预查；search 双 MATCH+LIKE 扫描。
6. 真实双 Agent OAuth 冷启动复验（M1 遗留）：M3b E6 用真 websockets daemon-sim 复证了网关侧 gating/force-start；真 OAuth refresh 竞争仍依赖既有确定性单测，未在干净环境重新消耗完整双 Agent 对话，结论沿用未变。
7. ~~FTS trigram / keyset 分页 / pyright 清零~~ 均已收（FTS 随 M3b、keyset 批2、pyright 批3）。
8. ~~`task #n` refs UI 消费面 / P11·P3 看板抽 `<TaskBoard>`~~ 顺手评估未做——refs→迷你 chip 与 TaskBoard 抽取属独立小重构，M4 或后续顺手件（非阻塞）。
