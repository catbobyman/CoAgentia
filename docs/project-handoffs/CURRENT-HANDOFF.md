# CoAgentia 当前交接（M4a 收口态）

| 项 | 内容 |
| --- | --- |
| 更新 | 2026-07-10，块 M4a「沉默提醒与循环 Reminder」收口并提交后重写（历次增补已沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本文只保留当前态） |
| 定位 | **当前唯一有效的交接入口**（README 约定 1/2）：新会话先读本文；历史背景读 PROJECT-RECORD；里程碑任务书均在 [archive/](archive/)（M4-HANDOFF 仍在 handoffs/，M4b 未收口不移档） |
| 一句话状态 | **M1/M2/M3 收口 + M4a 收口**（D5 沉默提醒升级链 + 循环 Reminder/LoopContract 解锁，实机 16/16）；**接续 = 块 M4b「freshness 与 HeldDraft」**（M4 出口前半：held 场景卡片可见+放行 1 分钟交付；任务书 [M4-HANDOFF.md](M4-HANDOFF.md) §9b） |

## 1. 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia`（monorepo：apps/server·web·daemon·mock-server + packages/contracts·contracts-ts·fixtures）；**无 git remote，全部提交仅存本地** |
| 分支 / HEAD | `main` / `01ff2d1 M4a complete: silence reminders & recurring reminder`（+ 本次 docs 收口提交）；工作树干净 |
| 提交链（近） | `080ed44` M3b → `559857e` 交接重写 → `e177328` M4 立项(任务书+契约修订) → **`01ff2d1` M4a 收口(F0–F4+B-M4-1+code-review)** |
| 测试基线 | 后端 **542 passed / 3 skipped**（`uv run pytest -q`）· web vitest **89** · pyright **0**（并入 `pnpm typecheck`）· ruff 干净 · `pnpm gen` 确定（两跑 diff 空）· 双侧 build 绿 |
| 契约版本 | A **v1.0.5**（held_drafts 载荷列+单活动行）· B **v1.2**（§10 护栏与沉默提醒规范 + §4.14 held 三键 + HELD_DRAFT_RESOLVED）· C · D **v1.0.1**（重评估组合+GC held 豁免）· E **v1.3**（M4 零新 Agent 工具，create_reminder 扩 loop_contract）；事实源 = `D:\Project4work\Agenthub_7_8\engineering_docs\` 五契约 + `docx_agenthub\CoAgentia-PRD.md` |
| 建表批次 | 0001 M1（17 表）→ 0002 M2 → 0003 M3（task_contracts/canvas_nodes/canvas_edges）→ 0004 files 索引 → 0005 messages_fts trigram → **0006 M4 held_drafts**（+ 活动行分区唯一索引）；held_drafts 表建齐但**读写面属 M4b**（freshness 门/三键端点未 serve） |
| 实机证据 | [M4A-EVIDENCE.md](../verify/M4A-EVIDENCE.md)（探针 16/16 + 3 截图 + console 0；沉默链 + 循环 Reminder）；[M3B-EVIDENCE.md](../verify/M3B-EVIDENCE.md) 等此前各批同目录 |

## 2. 里程碑总览（详情 = PROJECT-RECORD 对应节）

| 里程碑 | 出口 | 收口 |
| --- | --- | --- |
| M1 契约+实现+hardening | 真实两 Agent 对话/文件产出/reminder | `f2c993f` 合 main（§2–§5） |
| M2 任务与看板 | 番茄钟全流程（人发任务→Agent 认领交付→done） | `6c12b90`+`cdb27db`（§6） |
| M3a 契约与校验 | L2 契约链路（提交/修订链/request-draft/T7 门/升格） | `d5f092e`（§7） |
| 挂账三批 | 附件卡数据源 / keyset 分页 / pyright 清零 | `58b89b5`/`9331698`/`0b61669`（§8） |
| **M3b 画布与 gating** | **PRD M3 出口**：画布建图/成环拒/blocked 推导+投递 gating/force-start/React Flow/FTS trigram | **`080ed44`（§9）** |
| **M4a 沉默提醒与循环 Reminder** | D5 沉默提醒升级链 + 循环 Reminder/LoopContract 解锁（实机 16/16） | **`01ff2d1`（§10）** |

## 3. 系统当前能力面（一览）

- **IM 基座**（M1）：频道/DM/线程/@mention/文件/已读；真 daemon（Claude Code 适配器）双 Agent 对话；WS 事件驱动无刷新。
- **任务域**（M2）：as_task/convert 建任务、claim/assign/状态机（TASK_TRANSITIONS 单一事实源）、看板 P3/P11、搜索三分组、Activity、daemon MCP 六工具、usage 归属。
- **L2 契约**（M3a）：TaskPlan/TaskHandoff 提交与修订链、Agent 起草 request-draft S1 直投、T7 流转门（l2→in_review 校验 handoff）、升格 PATCH level l1→l2。
- **编排画布**（M3b）：每频道画布页签（React Flow）——节点=任务（agent 节点=第三创建途径，建 L2+锚点消息）、边=依赖（写事务拓扑排序防环）、基线快照指纹推进；**blocked 实时推导**（`kernel/graph.py` 权威 + 前端 `lib/graph.ts` 镜像，`golden/graph.json` 双跑对照）；**投递层 gating**（blocked 任务线程消息不唤醒、不入投递批、read_position 水位不越过）；**force-start**（仅人类、双留痕、不改状态、本次放行）；看板 blocked 徽标。
- **中文检索**（浮动件）：messages_fts trigram（≥3 字 MATCH + <3 字 LIKE 兜底，元字符转义）。
- **护栏与提醒**（M4a）：**D5 沉默提醒升级链**（tasks/silence.py 防自激 last_activity + hub 后台扫描：三态阈值提醒 Todo→创建者/InProg→owner/InReview→频道人类 → 升级主流消息 + activity silence_escalation → 升级后静默；task_events 纯推导无状态列）；**循环 Reminder**（create_reminder 内联 LoopContract 建即生效 + task_contracts 挂接行 + `interval.next_after` 塌缩式重排防重放风暴）；前端 P6 Reminders 页签 + Activity 置顶。**held_drafts 表建齐但 freshness/三键属 M4b。**

## 4. 接续 = 块 M4b「freshness 与 HeldDraft」（M4 出口前半）

**唯一任务书入口 = [M4-HANDOFF.md](M4-HANDOFF.md) §9b**（F5 freshness 门+held 域端点 / F6 G4 定时+G5 升级+GC 豁免 / F7 端到端实机 ＋ B-M4-2 held 卡三键）。**出口验收**（PRD §8 M4 行前半）：制造一次 held 场景——卡片可见（草稿全文+原因+倒计时）、放行 1 分钟内交付。

- **契约已就位**（M4a F0 已登记，M4b 零新契约）：held-drafts 端点组（§4.14）、`MessageHeld` 202、`HeldDraftReleaseResponse`/`HeldDraftResponse`、`HeldDraftRow.file_ids/as_task/reasons.total_unread`、`ErrorCode.HELD_DRAFT_RESOLVED`、`held_drafts` 表（0006，含 `uq_held_drafts_active`）——全部已在 contracts + 库。
- **关键教训（M4-HANDOFF §7/§8 已记）**：freshness 门位次 = 全部既有校验之后、落库之前；release 跳过 freshness 复查、原载荷（file_ids/as_task）落消息；**G4 重评估必须走正常 deliver 推进 read_position**（仅直投必复扣死循环，B §10.3）；held 单活动行分区唯一索引已建（同 scope 再扣=同行 held_count+1）；staging GC 需豁免活动 held 引用（D §9.2）。
- **M4a 收尾提示**：recurring reminder 首次触发在建后一个 interval（非建即触发，code-review 修）；沉默/reminder 系统消息发射经 hub `_post_system_message` 单点（M4b held 系统消息可复用）。

## 5. 挂账清单（非阻塞，勿当漏项重新发明）

| 项 | 说明 | 归属 |
| --- | --- | --- |
| ~~`_emit_activity` 迁 service 层~~ | **已收（M4a F2）**：迁 `activity/service.py`（conn 注入式 `emit_activity(tx, ...)`，hub 后台可调、提交后广播） | ✅ M4a |
| ~~`patch_task` 无法清空 `silence_override_h`~~ | **已收（M4a F2）**：白名单式 null 清除（`silence_override_h` 可清、不误伤 title） | ✅ M4a |
| 性能小批 | hub `usage.batch` 逐事件 SELECT（可批内 IN 预查）；search 双 MATCH+LIKE 扫描 | 独立小批 |
| `task #n` refs 无 UI 消费面 | refs 落库但引用消息不渲染任务 chip | 顺手评估 |
| P11/P3 看板双实现抽 `<TaskBoard>` | blocked 徽标已同构两份，抽共享组件可收 | 顺手评估 |
| messages_fts 键于 messages.rowid | VACUUM 会失同步（external-content 结构性约束，trigram 重建未改变） | 观察项 |
| OAuth 冷启动复验 | M1 遗留；M3b 用真 websockets daemon-sim 复证网关侧，真双 Agent OAuth refresh 竞争仍依赖既有确定性单测 | 择机 |

## 6. 启动方式

**真实开发**：终端 1 `uv run coagentia-server`（8787）；终端 2 `pnpm --filter @coagentia/web dev`（5173，代理 /api→8787）。
**同源构建**：`pnpm --filter @coagentia/web build` 后 `uv run coagentia-server`，开 `http://127.0.0.1:8787`（自动发现 apps/web/dist；异地部署设 `COAGENTIA_WEB_DIST`）。
**Mock**（显式开启才用）：`VITE_API_BASE=http://127.0.0.1:8642` + `VITE_MOCK_MODE=true` + mock-server。
**隔离实机 verify 范式**：临时库 `COAGENTIA_ALEMBIC_URL` alembic head + seed + 注入测试 key + 独立端口（8799 先例）；参照 M3B-EVIDENCE 与 scratchpad launcher 脚本体例。

## 7. 守门命令（全绿才算收口）

```
uv run pytest -q                    # 542 passed / 3 skipped 基线，零回归
pnpm -F @coagentia/web test         # vitest 89 基线，只增不减
pnpm typecheck                      # 含 pyright（0 错，新债即红）+ 双 tsc
uv run ruff check .
pnpm gen                            # 后 git diff 应为空（生成物确定性）
pnpm -F @coagentia/web build
```

## 8. 注意事项

- **无 git remote**：所有提交仅在本地 main，如需备份/协作须先 `git remote add` 并 push（可选项，owner 决定）。
- **环境要求**：SQLite ≥ 3.35（RETURNING）；真 claude CLI 踩坑见记忆/PROJECT-RECORD（stream-json `--verbose` 必需、须排空 stderr 等）。
- **迁移纪律**：新迁移按批次显式点名建表（勿 metadata.create_all 全集——坑1）；给既有表加索引/约束须 `if_not_exists`。
- **纪律 8（图算法单源）**：改动无环/blocked 语义必须同步 `kernel/graph.py` + `lib/graph.ts` + `golden/graph.json` 三处，两侧靠同一判例集守门。
- **gating 语义要点**（M3b code-review 教训）：「gating 作用于投递层」= 唤醒触发 **和** 投递批双面——held 消息须从投递批剔除且 read_position 水位不越过它，否则被兄弟消息顺带消费；M4 freshness/held 若挂同一投递层，沿用 `_filter_gated` 范式。
- 实机验证起的 server/浏览器/daemon-sim 进程结束前应关闭（8799 等端口）。
