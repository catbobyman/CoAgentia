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
| 接续 = 块 M3b 画布与 gating | **M3 里程碑已收口（§9a+§9b 全绿）；接续 = M4**（freshness/HeldDraft/沉默提醒/LoopContract 生成与循环 Reminder） |

## 当前接续任务

1. **M4 里程碑已整体收口（§10 M4a + §11 M4b = PRD M4 出口达成，`1052ee6`，实机 38/38 + code-review 17 CONFIRMED 全处理）**。M1–M4 全部收口，**无待完成里程碑**；接续 = M5+（cron cadence / Codex 适配 / 通知设置 / fail_closed 落地 / O2 接线，PRD §8，尚未立项）。M4-HANDOFF 已移 archive/。
2. ~~`_emit_activity` 迁 service 层~~ **已收（M4a F2）**：迁 `activity/service.py`（conn 注入式，hub 后台可调、提交后广播）。~~`patch_task` 清空 silence_override_h~~ 亦已收（同 F2，白名单式 null 清除）。
3. 独立性能小批（不阻塞）：hub `usage.batch` 逐事件 SELECT 可批内 IN 预查；search 双 MATCH+LIKE 扫描。
4. 真实双 Agent OAuth 冷启动复验（M1 遗留）：M3b E6 用真 websockets daemon-sim 复证了网关侧 gating/force-start；真 OAuth refresh 竞争仍依赖既有确定性单测，未在干净环境重新消耗完整双 Agent 对话，结论沿用未变。
5. ~~FTS trigram / keyset 分页 / pyright 清零~~ 均已收（FTS 随 M3b、keyset 批2、pyright 批3）。
6. ~~`task #n` refs UI 消费面 / P11·P3 看板抽 `<TaskBoard>`~~ 顺手评估未做——refs→迷你 chip 与 TaskBoard 抽取属独立小重构，M4 或后续顺手件（非阻塞）。
