# M4 护栏与提醒 —— 任务书（M4-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-10，M3 收口（`080ed44`）后立项；契约修订核对与落笔已随本次立项**先行完成**（§1） |
| 用途 | **M4 里程碑的唯一任务书入口**：把护栏（freshness/HeldDraft）与提醒（沉默升级链/循环 Reminder）装进系统。前置任务书 [M3-HANDOFF.md](archive/M3-HANDOFF.md) 已完成归档 |
| 上游事实源 | [engineering_docs/](../../../engineering_docs/README.md) 五契约（**A v1.0.5 / B v1.2 / C — / D v1.0.1 / E v1.3，M4 修订已全部落笔**，见 §1）· [CoAgentia-PRD.md](../../../docx_agenthub/CoAgentia-PRD.md) §4.5 护栏 G1–G6 / §4.7 沉默提醒 D5 / §4.3 LoopContract / §8 里程碑 · [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)（M3 收口态） |
| 执行计划 | 待建：开工首会话按本任务书建 `M4-DEV-PLAN.md`（体例同 [M3-DEV-PLAN.md](M3-DEV-PLAN.md)） |
| 出口标准 | **PRD M4 出口双场景**：制造一次 held 场景（卡片可见、放行 1 分钟内交付）+ 制造一次沉默任务（提醒与升级触达）（§9 逐条验收清单） |

---

## 0. 一句话目标

M3 让系统"看得见流程、拦得住跳步"；M4 给系统装上**可见可干预的护栏**与**防静默死亡的提醒链**：Agent 起草的消息若目标线程有它未读的新上下文，发送被扣成 HeldDraft 卡片（草稿全文+结构化原因），人类三键干预（放行/丢弃/令重评估）、5 分钟超时自动重评估、连扣 3 次升级喊人且**结构上不存在死循环**；没人推进的任务按状态阈值先提醒后升级；循环 Reminder 凭 LoopContract 解锁——护栏从"黑洞"变成"卡片"，任务从"静默死亡"变成"被催着走"。

## 1. 契约修订摘要（**已全部落笔**，2026-07-10 随立项完成——纪律 1 的完成态而非待办）

| 契约 | 版本 | 本次修订内容 | 状态 |
| --- | --- | --- | --- |
| A 实体表 | v1.0.4 → **v1.0.5** | `held_drafts` 增载荷列 `file_ids`/`as_task`（放行"原样发送"不丢附件/建任务意图，且封死 as_task 绕门旁路）；`HeldDraftReasons` 收紧（unread_message_ids 上限 50 + `total_unread`）；**活动行唯一**不变量（同 (agent,channel,thread) 至多一活动行，分区唯一索引，先例 uq_task_contracts_active）。核对确认：channels 阈值列/tasks 两列/task_events kind 目录均已建——**0006 仅建 held_drafts 一张表** | ✅ 已落笔 |
| B REST | v1.1 → **v1.2** | 新 **§10 护栏与沉默提醒规范条文**（freshness 判定/held 生命周期与关联规则/重评估组合语义/G4/G5/D5 判定/循环 Reminder）；新 **§4.14** held-drafts 端点组（清单 + release/discard/reevaluate 三键，仅人类）；`POST /reminders` 扩 inline `loop_contract`；错误码 +`HELD_DRAFT_RESOLVED`（22→23） | ✅ 已落笔 |
| C WS | — | **无修订**：`held_draft.created/updated`、`reminder.created/updated`、activity 升级类 kind M1 已登记（payload 含 EVENT_PAYLOADS 分派），M4 只是开始发射 | ✅ 核对完 |
| D daemon | v1.0 → **v1.0.1** | 编辑性：§4.4 #7 补**重评估组合语义**挂接（wake + deliver 积压 + guard_feedback 直投——deliver 推进游标防复扣死循环；WakeReason 不扩枚举）；§9.2 staging GC 增 **held 引用豁免**。无帧形状/指令目录变更 | ✅ 已落笔 |
| E 适配器 | v1.2 → **v1.3** | M4 工具组裁决：**零新增 Agent 工具**——held 感知 = send_message 202 透传 + guard_feedback 等待（v1.0 冻结形状本批兑现）、三键仅人类、沉默提醒走系统消息投递；唯一变化 = `create_reminder` 扩可选参数 `loop_contract`；daemon/适配器侧**零改动** | ✅ 已落笔 |

> **纪律 1 中间态**：契约文档修订已先行完成；contracts 包 manifest 同步 = F0 模块（开工第一步之一），任何业务实现之前完成。

## 2. 范围与非目标（勿扩界）

**范围**（PRD §8 M4 行 + M3 移交件）：

- **freshness check（G1）**：`POST /channels/{id}/messages` 对 Agent 主体加门——目标 scope（线程或频道主流）存在未读（read_position 之后他人消息）→ 消息不落库、建/更新 held 行、响应 `202 {held_draft}`（B §10.1；人类与系统消息永不 held）
- **HeldDraft 卡片（G2）**：held 行实时出现在目标位置（WS `held_draft.created/updated`），卡片 = 草稿全文 + 结构化原因（未读清单可点跳转）+ 倒计时（`next_reeval_at` 本地读秒不推帧）
- **三键干预（G3）**：release（原载荷发送、跳过 freshness 复查）/ discard（终态 + guard_feedback 直投告知）/ reevaluate（B §4.14；**仅人类**，Agent 403 rule=G3）
- **超时自愈（G4）**：hub 扫描 `next_reeval_at` 到点自动触发重评估组合（wake + deliver 积压 + guard_feedback 直投——**deliver 推进游标是防死循环的关键**，B §10.3）
- **升级喊人（G5）**：`held_count` 达 `channels.held_escalate_n` → escalated_at + scope 系统消息 @人类 + activity `held_escalation` 置顶 + **停自动重评估**（死循环的结构性终点，B §10.4）
- **G6 诊断**：guard.held/released/discarded/reevaluate_requested/escalated 全留痕（DIAGNOSTIC_TYPES 常量已备）
- **沉默提醒 + 升级链（D5）**：hub 扫描 todo/in_progress/in_review 超阈值任务（`channels.remind_*_h`，`tasks.silence_override_h` 覆盖）→ 任务线程系统消息第一次提醒（@创建者/@owner/@频道人类）→ 再超期升级（频道主流系统消息 + activity `silence_escalation` 置顶）；留痕走 `task_events(reminder_sent/escalated)` 纯推导无状态列（B §10.5）
- **循环 Reminder 解锁**：`POST /reminders` 接 inline LoopContract（同事务建 task_contracts 挂接行）；`run_reminder_scan` 补 recurring 按 interval 重排（现状：recurring 一律置 done 的缺口）；`create_reminder` MCP 工具扩参（B §10.6 / E v1.3）
- **M4 建表批次**（A §5）：`held_drafts`（0006，一张表）
- **M3 移交挂账（M4 开工第一步）**：`_emit_activity` 迁 service 层（hub 后台路径要发射升级类 activity，无法 import 路由层私有函数——已两次顺延勿再跳过）；`patch_task` 修 `silence_override_h=null` 被 `if v is not None` 丢弃无法清除覆盖

**非目标**：fail_closed activity 与落地批次（M6）· Orchestrator 升级接线（O2 默认关，M6）· **P12 阈值设置 UI**（PATCH /channels 阈值组字段 M1 已全量支持，出口不需要 UI——挂账 §8）· cron cadence（M5+，MVP 仅 interval）· 每频道通知设置（M5）· Codex 适配（M5）。

### 2.1 执行切分（两块竖切，体例同 M2/M3）

| 块 | 定位 | 模块 | 收口意义 |
| --- | --- | --- | --- |
| **M4a 沉默提醒与循环 Reminder** | 先收 | F0 · F1 · F2 · F3 · F4 ＋ B-M4-1 | **D5 链路独立可验证**（出口后半句：制造沉默任务、提醒与升级触达）；挂账双修与建表地基全在本块，中途变故也有完整可演示成果 |
| **M4b freshness 与 HeldDraft** | 块 a 收口后开工 | F5 · F6 · F7 ＋ B-M4-2 | **收口即 PRD M4 出口**（held 场景卡片可见、放行 1 分钟内交付）；只消费块 a 产物（activity service 化、0006 表、契约登记），零回向依赖 |

- 切分依据：块 a 全是**时间驱动的后台扫描与提醒链**（hub loop 心智：判定材料→系统消息→留痕→升级），块 b 全是**请求路径拦截与干预流**（freshness 门/三键/直投组合）——两类心智不混批。
- held_drafts 表在 F1 一次建齐（块 a 期间空置——M3"迁移不拆两次"先例）。

## 3. 现有资产盘点（拿来即用，勿重复建设；2026-07-10 逐项实核）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| HeldDraft 三形状 + 枚举 | contracts entities.py:453-480 / enums.py:143-154 | HeldDraftReasons/Row/Public + HeldDraftStatus/HeldResolution **已冻结**（F0 按 A v1.0.5 增 file_ids/as_task/total_unread） |
| 202 held 响应模型 | contracts rest.py:261-264 | `MessageHeld {held_draft}` 已登记——freshness 门的响应形状现成 |
| WS 三族事件 | contracts ws.py | held_draft.* / reminder.* / activity.* 全部登记含 EVENT_PAYLOADS 分派；前端 wsBridge 未知 type 忽略原则保证增量接入 |
| guard.* 诊断类型 + "Draft held" 活动文案 | contracts constants.py:79-84 / 19-29 | G6 五类诊断 + server 合成活动细分文案**常量已备** |
| TaskEventKind.REMINDER_SENT / ESCALATED | contracts enums.py:90-91 | **D5 留痕载体已登记**——提醒/升级历史进 task_events 纯推导，不加 tasks 列 |
| LoopContractBody + CONTRACT_BODY_MODELS | contracts rest.py:412-430 | M3a 已建齐（"生成消费归 M4"），F4 直接 model_validate |
| channels 阈值列 + ChannelPatch | server models.py:228-233 / contracts rest.py:208-219 | remind_todo/inprog/review_h(24/12/24)、remind_escalation、held_reeval_min(5)、held_escalate_n(3) **M1 已建**且 PATCH 端点全字段支持——**P12 配置管道现成，M4 零建设** |
| tasks.silence_override_h / status_changed_at | server models.py:450-451 | M2 已建（"D5 M4 才消费"/"沉默提醒计时锚"） |
| task_contracts.reminder_id | server models.py:535（0003 已建） | XOR CHECK 已落；**刻意不落 FK**（跨批引用惯例）——循环引用问题不存在，F4 直接挂接 |
| reminder 扫描循环 | server hub.py:1086-1162 | once 全链路在线（锚点系统消息+mention 视同 @+唤醒经 bus）；**recurring 缺口 = 一律置 done**（F4 补 interval 重排） |
| 直投桥范式 | server hub.py:963-986 | `inject_contract_draft_request`（MessageInjectData → send_instr → `_run_sync` 等 ack）——guard_feedback 直投同路复制 |
| 投递/游标机制 | server hub.py:610-722 | `_write_read_position`（仅 ack DONE）、`_filter_gated`、`_deliver_backlog`——G4 组合的 deliver 积压复用正常路径 |
| freshness 门位 | server routes/messages.py:316 | `acting_member` 已解析（`kind=="agent"` 即门条件）；门插在既有校验之后、落库(353)之前 |
| `_emit_activity` | server routes/messages.py:117-145 | F2 迁移对象；`_generate_activity`(148-200) 仅 mention/dm，升级类生成在 F3/F6 |
| daemon 侧 202 透传 + inject 渲染 | daemon adapters/mcp.py:7-8 / encoding.py:63-70 | **零改动**：202 held 结构化透传形状 M1 预置；guard_feedback 来源标注渲染已备（E v1.3 结论） |
| Activity 置顶区 | web ActivityScreen.tsx | `ESCALATION_KINDS` 三值 + "─ 需要处理 ─" 置顶渲染**结构就位**（"M2 恒空"）——只缺后端实产出 |
| 一致性测试双跑 | server tests/test_conformance_dual.py | M4 端点照 M2/M3 先例扩进去（纪律 3） |

**确认缺口**（施工面）：held_drafts 表（0006）· ENDPOINTS_M4 与 held 干预模型 · freshness 门与三键端点 · D5/G4 扫描逻辑 · recurring 重排 · HeldDraftCard 组件 · P6 Reminders 页签 · wsBridge held_draft.*/reminder.* case（现落 default 被丢）。

## 4. 线 A：后端任务分解（模块 → DoD）

| # | 块 | 模块 | 内容（契约出处） | 完成判据（DoD） |
| --- | --- | --- | --- | --- |
| F0 | **M4a** | 契约登记同步（**开工第一步之一**） | contracts 包：`ENDPOINTS_M4`（§4.14 四端点）、held 干预响应模型（release 响应 `{message, held_draft}` 等）、`HeldDraftRow/Reasons` 按 A v1.0.5 增字段、`ReminderCreate` 扩 `loop_contract`、ErrorCode +HELD_DRAFT_RESOLVED、`create_reminder` MCP 工具 schema 扩参描述（E v1.3）；mock 补 `GET /held-drafts` 形状（纪律 4） | manifest/catalog 测试红转绿；`pnpm gen` diff 为空；mock 一致性扩展全绿 |
| F1 | **M4a** | 0006 迁移 | Alembic `0006_m4`：`held_drafts` 一张表一次建齐（块 a 期间空置）＋**活动行分区唯一索引**（`COALESCE(thread_root_id,'')` 表达式，`sqlite_where=status IN ('held','reevaluating')`，先例 uq_task_contracts_active）；models.py ORM + `M4_TABLES` 常量 | 从零 `upgrade head` 与 M3 库增量升级双路绿；表结构对照测试扩展 |
| F2 | **M4a** | 挂账双修（**与 F0/F1 并行无依赖**） | ① `_emit_activity` 从 routes/messages.py 迁 `activity/service.py`（conn 注入式签名——hub 后台无 request 上下文；routes 与 hub 共同消费，WS 广播路径一并抽走）；② `patch_task` 用 `model_fields_set` 区分「未提供」与「显式 null」，`silence_override_h=null` 可清除 | 迁移后 M2/M3 activity 全量回归零变化；patch null 清除 + 不误清其他字段的回归测试 |
| F3 | **M4a** | D5 沉默扫描与升级链 | hub 新后台判定（与 reminder 扫描同节奏）：B §10.5——last_activity = max(status_changed_at, 线程最新**非系统**消息, task_events 排除 reminder_sent/escalated)（**防自激**）；阈值按状态取 remind_*_h、silence_override_h 三态同值覆盖；第一次提醒 = 任务线程系统消息（Todo→@创建者 / In Progress→@owner / In Review→@频道人类）+ mention 行 + `task_events(reminder_sent)`；升级 = remind_escalation 开 且 最新 reminder_sent 后再超期 → 频道主流系统消息 + activity `silence_escalation`（经 F2 服务）+ `task_events(escalated)`；升级后静默、新活动重置整链 | 判定逐例：三态阈值/override/开关关/自激防护（提醒产物不刷新 last_activity）/升级后静默/新活动重置；@Agent owner 的提醒触发唤醒断言（mention 触发既有路径） |
| F4 | **M4a** | 循环 Reminder + LoopContract | B §4.4/§10.6：`POST /reminders` 扩 `loop_contract`（recurring 缺失 422 回归不变、once 携带 422、LoopContractBody model_validate、cadence 一致校验、**同事务**建 task_contracts(kind=loop_contract, reminder_id) 挂接行并回填 loop_contract_id）；`run_reminder_scan` recurring 分支：触发后 `next_fire_at += interval` 保持 active；cadence 仅 interval（ISO-8601 duration 解析） | recurring 创建/触发/重排/再触发/取消全链路；once 语义零回归；挂接行 XOR 约束路径；MCP create_reminder 扩参透传（daemon 侧零改动断言） |
| F5 | **M4b** | freshness 门 + held 域端点 | B §10.1/§10.2/§4.14：post_message 在既有校验后、落库前对 Agent 主体判 scope 未读 → 建/更新 held 行（**同 scope 单活动行**：再扣=同行 held_count+1 回 held）→ `202 MessageHeld` + `held_draft.created/updated` + server 合成 `Draft held` activity 细分 + `guard.held`；四端点：GET 清单 / release（原载荷落消息**跳过 freshness**、基础校验重跑、file_ids 绑定 + as_task 原子执行、作者=agent）/ discard（终态+guard_feedback 直投）/ reevaluate（置 reevaluating+组合触发）；三键仅人类 403 rule=G3；终态干预 409 HELD_DRAFT_RESOLVED 携最新态；直投离线 503（release 不依赖 daemon） | freshness 逐路径（§9b.8）；三键逐路径 + 并发再扣唯一索引兜底；release 后消息经正常投递引擎触发接收方（gating 回归不受扰——freshness 是发送侧护栏与投递 gating 无关，B §8.1/D §8.1 双确认） |
| F6 | **M4b** | G4 定时 + G5 升级 + GC 豁免 | B §10.3/§10.4：hub 扫描 `status='held' AND next_reeval_at<=now AND escalated_at IS NULL` → 重评估组合（`agent.wake`(channel_message) + `message.deliver` 积压 + `message.inject`(guard_feedback)，置 reevaluating + guard.reevaluate_requested）；再扣路径判 held_count ≥ held_escalate_n → escalated_at + scope 系统消息 @人类 + activity `held_escalation` + guard.escalated + **停自动重评估**（人工 reevaluate 仍可、不二次喊人）；files/gc.py 增活动 held 行 file_ids 引用豁免（D §9.2 v1.0.1） | 组合触发断言**含游标推进**（deliver ack 后 read_position 前移→重发过门）；仅直投不投积压的死循环反例测试；升级判定/停自动/不二次喊人；GC 豁免 + 终态后回收 |
| F7 | **M4b** | 端到端实机 verify（**块 b 收口 = PRD M4 出口**） | 真机（隔离库+独立端口+daemon-sim，M3B 范式）：**held 场景**——Agent 起草期间线程进新消息 → 202 → 卡片实时可见（草稿全文+原因+倒计时）→ 放行 → 消息落库且对方 **1 分钟内**收到投递；discard/reevaluate 路径 + G4 超时自动 + G5 连扣升级（@人类可见+置顶+停自动）；**沉默场景**——阈值置 0h 制造超期 → 线程提醒消息@触达 → 再超期 → 主流升级消息 + Activity 置顶；循环 reminder 到点触发与重排；全程 WS 无刷新 | §9a+§9b 清单收口 + 截图/证据归档 `docs/verify/M4-EVIDENCE.md` |

**推进顺序**：**块 M4a**：F0 ∥ F1 ∥ F2 并行（文件域不相交：contracts 包 / migrations / routes+service 重构）→ F3 → F4 串行（F3 消费 F2 的 activity 服务；F4 独立可与 F3 并行）；**块 M4b**：F5 → F6 串行（G4/G5 依赖 held 行语义）→ F7 收口。

## 5. 线 B：前端任务分解（模块 → DoP）

> 沿用设计线 verify SOP：优先消费归档稿 `docx_agenthub/04-设计稿/afterglow-ds/previews/`（held 卡有 C1 特写稿则对照；无则按契约字段 + DS token 组装，token 零发明）→ playwright 1440×900 对照 → 复发点自查。

| # | 块 | 模块 | 内容 | 完成判据 |
| --- | --- | --- | --- | --- |
| B-M4-1 | **M4a** | P6 Reminders 页签 + Activity 置顶接真 | Agent 详情 Reminders 页签（锚点列/loop contract 摘要/kind/next_fire/取消操作——P6 规则：创建者的创建者或 admin）；wsBridge 补 `reminder.created/updated` case；Activity 置顶区随 F3 实产出接真（结构已就位零新建）；沉默提醒/升级系统消息在消息流自然渲染验证 | 行为测试 + 屏对照；置顶区实数据截图 |
| B-M4-2 | **M4b** | HeldDraft 卡片 + 三键 | 线程/主流 held 卡（CardKind.HELD_DRAFT）：草稿全文（长文折叠）+ 原因清单（unread 可点跳转 + total_unread 计数）+ 倒计时（next_reeval_at 本地读秒**不推帧**，契约 C §6 注明）+ 三键（仅人类可见）+ 升级横条（escalated_at 非空）+ 终态折叠回执；wsBridge 补 `held_draft.created/updated` case；409 HELD_DRAFT_RESOLVED → 以响应最新态刷新卡片 | 行为测试（三键/倒计时/终态回执/409 刷新）+ 屏对照截图 |

## 6. 纪律（沿用 M2/M3 八条，本里程碑强调两点）

1. 契约 ↔ manifest 双向同步（F0 收口中间态）。
2. 生成物只经脚本重生成，diff 为空守门。
3. 一致性测试套件双跑复用：M4 端点直接扩进 `test_conformance_dual.py`。
4. mock 是形状源不是逻辑源：freshness 判定/held 状态机/D5 扫描只活在真 server。
5. 每完成一个模块：更新 M4-DEV-PLAN §进度表 + [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)；阶段结论沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问；已拍板勿再问。
7. 值域/判定语义只写一处：held 状态机边、freshness 判定、D5 阈值取值次序全走 contracts 常量/服务层单点，前端不复制判定。
8. **两个"作用层"勿混**（M3b 教训的 M4 变体）：投递 gating 作用于**投递层**（blocked 不唤醒），freshness 护栏作用于**发送侧 REST 端点**（B §8.1/D §8.1 双确认"与投递无关"）——held 消息从未落库故不存在投递批过滤问题；但 **G4 重评估必须走正常 deliver 推进游标**（B §10.3），这是两层唯一的交点，实现与测试都要盯死。

## 7. 本任务书裁决（实现按此执行；owner 可否决，否决处升契约版本回改）

| # | 裁决 | 依据 |
| --- | --- | --- |
| 1 | freshness scope = thread_root_id 非空→线程；空→频道主流；DM 同规则；人类与系统消息永不 held | B §10.1；PRD G1"目标线程"的最小外延 |
| 2 | 无 read_position 行 = scope 全量算未读（从未被投递过的频道里 Agent 主动发言，先读后说）；未读集空 → 放行 | B §10.1.3；保守面安全 |
| 3 | 同 scope 单活动 held 行：再扣 = 同行 held_count+1 回 held，不建新行；DB 分区唯一索引兜底并发 | A v1.0.5；held_count 语义（G5"同一草稿连续被扣"）的落地形 |
| 4 | 重评估 = wake + deliver 积压 + guard_feedback 直投三帧组合；WakeReason 不扩枚举（channel_message） | D v1.0.1 §4.4 #7 / B §10.3；inject 不动游标→仅直投必然复扣死循环 |
| 5 | 升级后停 G4 自动重评估（扫描排除 escalated_at 非空）；人工 reevaluate 仍可、其后再扣不二次喊人 | B §10.4；PRD G5"结构上不存在无限死循环"的机制落点 |
| 6 | release 原载荷发送（file_ids 绑定 + as_task 原子执行、作者=agent）**跳过 freshness 复查**、基础校验重跑 | B §4.14；G3"放行原样发送"= 人类显式背书 |
| 7 | D5 last_activity 排除系统消息与 reminder_sent/escalated 事件（防自激）；提醒/升级历史全在 task_events 纯推导，不加 tasks 状态列 | B §10.5；对齐 D 契约"从 DB 事实推导"纪律 |
| 8 | silence_override_h 非空 = 三态同一覆盖值（不做分态覆盖） | B §10.5.3；PRD"周报 7 天正常"单值语义 |
| 9 | LoopContract 随 create_reminder 原子创建**即生效，无人确认门**（区别任务契约）；cadence MVP 仅 interval，cron 归 M5+ | B §4.4/§10.6；reminder 只唤醒创建者、风险面小，人类干预面=P6 取消 |
| 10 | held 升级喊人 = scope 内系统消息 @频道全体人类成员（单人 MVP 即 owner） | B §10.4；PRD G5"@人类成员" |
| 11 | reasons.unread_message_ids 上限 50（保留最新）+ total_unread 真实计数 | A v1.0.5；繁忙频道未读无界，202 载荷与卡片需有界 |
| 12 | P12 阈值设置 UI 不进 M4（配置管道 M1 全通：ChannelPatch 全字段 + admin 门）；实机 verify 直接 PATCH 设阈值 | 出口不需要；挂账 §8 |
| 13 | freshness 门位次 = 全部既有校验之后、落库之前（被扣草稿放行时不得再触发校验类 4xx；release 时基础校验重跑兜状态漂移） | B §10.1.4 |

## 8. 挂账（承接 CURRENT-HANDOFF §5；勿当漏项重新发明）

| 出处 | 问题 | 归属 |
| --- | --- | --- |
| M2 二轮 review（两次顺延） | `_emit_activity` 在 routes 层 | **F2 收**（M4 开工第一步） |
| M3 收口 | `patch_task` 无法清空 `silence_override_h` | **F2 收** |
| M2 挂账 | hub usage.batch 逐事件 SELECT；search 双 MATCH+LIKE 扫描 | 性能小批，独立不阻塞 |
| M2 观察 | `task #n` refs 无 UI 消费面 | 顺手评估 |
| M2 挂账 | P11/P3 看板双实现抽 `<TaskBoard>` | 顺手评估 |
| M3 观察 | messages_fts 键于 rowid，VACUUM 失同步 | 观察项 |
| M1 遗留 | OAuth 冷启动复验 | F7 真机可顺路，结论单独记录 |
| **本任务书新增** | P12 阈值设置 UI（频道设置页暴露 remind_*/held_* 字段） | M5 或顺手小批（管道已通，纯 UI） |

## 9. M4 出口验收清单（按块分组；两块全绿即里程碑收口）

### 9a. 块 M4a「沉默提醒与循环 Reminder」清单

- [ ] 1. F0 契约登记：ENDPOINTS_M4 / held 干预模型 / HeldDraft 形状 v1.0.5 / ReminderCreate 扩参 / HELD_DRAFT_RESOLVED / mock 形状，catalog 与 `pnpm gen` 两跑一致
- [ ] 2. Alembic `0006_m4`：held_drafts + 活动行分区唯一索引；从零与增量双路绿；M4_TABLES 对照测试
- [ ] 3. 挂账双修：activity 服务化后 M2/M3 全量回归零变化、hub 可发射；patch_task null 清除生效且不误清他字段
- [ ] 4. D5 全路径：三态阈值提醒（目标各断言）→ 升级触达（主流消息 + 置顶 activity）；override 覆盖 / 开关关不升级 / 自激防护 / 升级后静默 / 新活动重置；@Agent owner 提醒触发唤醒
- [ ] 5. 循环 Reminder：inline loop_contract 建挂接行（XOR/一致校验/once 422）；到点触发 → interval 重排保持 active → 再触发；once 与取消零回归
- [ ] 6. B-M4-1：P6 Reminders 页签 + wsBridge reminder.* + Activity 置顶实产出截图
- [ ] 7. 块 a 守门：后端/前端全量测试、typecheck（pyright 0）、ruff、gen 确定、双侧 build 全绿；交接文档同步（纪律 5）

### 9b. 块 M4b「freshness 与 HeldDraft」清单（全绿 = **PRD M4 出口达成**）

- [ ] 8. freshness 门逐路径：Agent 有未读 → 202 + held 行 + WS + Draft held activity + guard.held；人类不扣 / 系统消息不扣 / 未读空放行 / 无游标全量未读 / 线程 vs 主流 scope 隔离 / 门位次（校验类 4xx 优先）
- [ ] 9. 三键干预：release 原载荷（附件 + as_task）落消息跳过 freshness、作者=agent；discard/reevaluate 直投（离线 503）；仅人类 403 rule=G3；终态 409 携最新态；并发再扣唯一索引兜底
- [ ] 10. G4/G5：定时组合触发**含游标推进断言**（deliver ack 后重发过门）；仅直投复扣的反例测试；再扣 held_count+1 → 达阈值升级（@人类 + 置顶 + guard.escalated）→ 停自动、人工可续、不二次喊人
- [ ] 11. GC 豁免：活动 held 引用的 staging 文件超 24h 不删、终态后回收
- [ ] 12. B-M4-2：held 卡全交互（草稿/原因跳转/倒计时/三键/升级横条/终态回执/409 刷新）+ wsBridge held_draft.*
- [ ] 13. **PRD M4 出口双场景真机实证**：held 场景（卡片可见、放行 1 分钟内交付）+ 沉默场景（提醒与升级触达）+ 循环 reminder 触发；全程 WS 无刷新；截图归档 `docs/verify/M4-EVIDENCE.md`
- [ ] 14. 终收口守门：全量测试绿（基线只增不减）；M4 阶段结论写入 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本任务书移入 archive/（README 维护约定 3）

## 10. 第一步建议

块 M4a 从 **F0 ∥ F1 ∥ F2** 三路并行开工（文件域不相交：contracts 包 / migrations / routes→service 重构）：F0 把 §1 已落笔的契约修订收进 contracts 包让 manifest 回到"文档=代码"一致态；F1 建 held_drafts 地基；F2 是两次顺延的挂账、纯重构零新语义。三者全绿后 F3（消费 F2）与 F4（独立）可并行推进；B-M4-1 契约形状就位（F0）后即可吃 mock 开工。**块 M4b 不与块 a 交错**——等 §9a 全绿再动，保持"一块 = 一个可交接的收口"。
