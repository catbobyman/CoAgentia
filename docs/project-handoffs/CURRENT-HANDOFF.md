# CoAgentia 当前交接（M4 里程碑收口态）

| 项 | 内容 |
| --- | --- |
| 更新 | 2026-07-10，**块 M4b「freshness 与 HeldDraft」收口 = M4 里程碑完成**后重写（历次增补已沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本文只保留当前态） |
| 定位 | **当前唯一有效的交接入口**（README 约定 1/2）：新会话先读本文；历史背景读 PROJECT-RECORD；里程碑任务书均在 [archive/](archive/)（M4-HANDOFF 已随 M4 收口移档） |
| 一句话状态 | **M1/M2/M3/M4 全部收口**——M4 = 护栏（freshness/HeldDraft 三键/G4 超时自愈/G5 升级喊人）+ 提醒（D5 沉默升级链 + 循环 Reminder）。M4b 实机 38/38 + `/code-review high` 17 CONFIRMED 全处理。**无待收口里程碑**（下一步 = M5+，见 PRD §8，尚未立项） |

## 1. 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia`（monorepo：apps/server·web·daemon·mock-server + packages/contracts·contracts-ts·fixtures）；**无 git remote，全部提交仅存本地** |
| 分支 / HEAD | `main` / `1052ee6 M4b complete: freshness & HeldDraft`（+ 本次 docs 收口提交）；工作树干净 |
| 提交链（近） | `e177328` M4 立项 → `01ff2d1` M4a 收口 → `dc650f5` M4a docs → **`1052ee6` M4b 收口(F5–F7+B-M4-2+code-review)** |
| 测试基线 | 后端 **572 passed / 3 skipped**（`uv run pytest -q`）· web vitest **106** · pyright **0**（并入 `pnpm typecheck`）· ruff 干净 · `pnpm gen` 确定（两跑 diff 空）· 双侧 build 绿 |
| 契约版本 | A **v1.0.5** · B **v1.2** · C · D **v1.0.1** · E **v1.3**（M4 全部修订已落笔并兑现，M4b 零新契约——纯消费 M4a F0 登记）；事实源 = `D:\Project4work\Agenthub_7_8\engineering_docs\` 五契约 + `docx_agenthub\CoAgentia-PRD.md` |
| 建表批次 | 0001 M1（17 表）→ 0002 M2 → 0003 M3 → 0004 files 索引 → 0005 messages_fts trigram → 0006 M4 held_drafts（+ 活动行分区唯一索引）；**held_drafts 读写面已在 M4b 全 serve**（freshness 门 + 三键端点 + G4/G5） |
| 实机证据 | [M4B-EVIDENCE.md](../verify/M4B-EVIDENCE.md)（探针 **38/38** + 3 截图 + console 0；held 场景放行 1 分钟交付 / discard / reevaluate 死循环破除 / G4 超时 / G5 升级 + code-review §5）；[M4A-EVIDENCE.md](../verify/M4A-EVIDENCE.md) 等此前各批同目录 |

## 2. 里程碑总览（详情 = PROJECT-RECORD 对应节）

| 里程碑 | 出口 | 收口 |
| --- | --- | --- |
| M1 契约+实现+hardening | 真实两 Agent 对话/文件产出/reminder | `f2c993f` 合 main（§2–§5） |
| M2 任务与看板 | 番茄钟全流程（人发任务→Agent 认领交付→done） | `6c12b90`+`cdb27db`（§6） |
| M3a 契约与校验 | L2 契约链路（提交/修订链/request-draft/T7 门/升格） | `d5f092e`（§7） |
| 挂账三批 | 附件卡数据源 / keyset 分页 / pyright 清零 | `58b89b5`/`9331698`/`0b61669`（§8） |
| **M3b 画布与 gating** | **PRD M3 出口**：画布建图/成环拒/blocked 推导+投递 gating/force-start/React Flow/FTS trigram | **`080ed44`（§9）** |
| **M4a 沉默提醒与循环 Reminder** | D5 沉默提醒升级链 + 循环 Reminder/LoopContract 解锁（实机 16/16） | **`01ff2d1`（§10）** |
| **M4b freshness 与 HeldDraft** | **PRD M4 出口**：held 场景卡片可见+放行 1 分钟交付 / 三键 / G4 超时自愈 / G5 升级喊人（实机 38/38） | **`1052ee6`（§11）** |

## 3. 系统当前能力面（一览）

- **IM 基座**（M1）：频道/DM/线程/@mention/文件/已读；真 daemon（Claude Code 适配器）双 Agent 对话；WS 事件驱动无刷新。
- **任务域**（M2）：as_task/convert 建任务、claim/assign/状态机（TASK_TRANSITIONS 单一事实源）、看板 P3/P11、搜索三分组、Activity、daemon MCP 六工具、usage 归属。
- **L2 契约**（M3a）：TaskPlan/TaskHandoff 提交与修订链、Agent 起草 request-draft S1 直投、T7 流转门（l2→in_review 校验 handoff）、升格 PATCH level l1→l2。
- **编排画布**（M3b）：每频道画布页签（React Flow）——节点=任务（agent 节点=第三创建途径，建 L2+锚点消息）、边=依赖（写事务拓扑排序防环）、基线快照指纹推进；**blocked 实时推导**（`kernel/graph.py` 权威 + 前端 `lib/graph.ts` 镜像，`golden/graph.json` 双跑对照）；**投递层 gating**（blocked 任务线程消息不唤醒、不入投递批、read_position 水位不越过）；**force-start**（仅人类、双留痕、不改状态、本次放行）；看板 blocked 徽标。
- **中文检索**（浮动件）：messages_fts trigram（≥3 字 MATCH + <3 字 LIKE 兜底，元字符转义）。
- **护栏与提醒**（M4a）：**D5 沉默提醒升级链**（tasks/silence.py 防自激 last_activity + hub 后台扫描：三态阈值提醒 Todo→创建者/InProg→owner/InReview→频道人类 → 升级主流消息 + activity silence_escalation → 升级后静默；task_events 纯推导无状态列）；**循环 Reminder**（create_reminder 内联 LoopContract 建即生效 + task_contracts 挂接行 + `interval.next_after` 塌缩式重排防重放风暴）；前端 P6 Reminders 页签 + Activity 置顶。
- **freshness 护栏与 HeldDraft**（M4b）：**freshness 门**（guard/service.py 判定单源——scope=线程/主流未读、仅 Agent 主体过门、幂等 hit 优先于门；扣草稿建/刷新 held 单活动行 SAVEPOINT 兜并发再扣，202 不落库）；**三键人类干预**（release 原载荷落消息不依赖 daemon+跳过 freshness / discard 直投 guard_feedback 离线 503 回滚 / reevaluate 委托 hub 死锁规避+终态守卫；仅人类 403 G3、终态 409 HELD_DRAFT_RESOLVED）；**G4 超时自愈**（hub run_held_scan 在线先探再翻 reevaluating + 组合 wake+deliver 推进游标+inject，deliver 推进 read_position 是防复扣死循环关键）；**G5 升级喊人**（held_count 达阈值→escalated_at+scope 系统消息@人类+held_escalation activity+停自动重评估）；GC 豁免活动 held 附件；前端 HeldDraftCard（草稿折叠/未读跳转/本地读秒倒计时/三键/升级横条/终态回执/409 刷新/非-409 toast）。

## 4. 接续 = M5+（M4 已收口，尚未立项）

**M1–M4 全部收口，无待完成里程碑。** 下一步 = PRD §8 M5+（cron cadence / Codex 适配 / 每频道通知设置 / fail_closed 落地批 / Orchestrator 升级接线 O2 等，均属 M4 非目标顺延）——尚未立项。新里程碑开工按 README 约定 3 建 `M5-HANDOFF.md` 任务书（体例同 [archive/M4-HANDOFF.md](archive/M4-HANDOFF.md)），并先行核对/落笔契约修订（纪律 1）。

- **M4 收官锚点**：held 判定/值域单一事实源 = `guard/service.py`（`ACTIVE_STATUSES`/`TERMINAL_STATUSES`/`compute_unread`/`freshness_hold`）；三键端点 = `routes/held_drafts.py`；G4/G5/reevaluate 桥 = `hub.py`（`run_held_scan`/`reevaluate_held`/`_held_reevaluation_combo`）；死锁规避范式（路由只读+委托 hub 独立已提交 tx）与「在线先探再翻状态」是两处易踩的坑（见 M4B-EVIDENCE §5 #5/#6）。

## 5. 挂账清单（非阻塞，勿当漏项重新发明）

| 项 | 说明 | 归属 |
| --- | --- | --- |
| ~~`_emit_activity` 迁 service 层 / `patch_task` 清 `silence_override_h`~~ | **已收（M4a F2）** | ✅ M4a |
| **held 系统消息骨架 / human_members 跨模块重复**（评审 #8/#9） | `_post_system_message`（hub「三处共用」+ guard 第 4 份 + channels 瘦变体）、`_channel_human_members`（hub enum 版 + guard string 版）——确认 DRY 债**非 bug**；消除需抽中立共享模块并触及承重 reminder/silence 路径，收口期 ROI 低 | 顺手小批（新会话可做，tests 守回归） |
| held 卡「重评估中…」显示边角 | 升级态 held 行 next_reeval_at 已过时倒计时组件显示「重评估中…」而 status 实为 held（正常场景 G4 会真翻转，此显示才准） | 观察项（评审 #2 观察） |
| 性能小批 | hub `usage.batch` 逐事件 SELECT（可批内 IN 预查）；search 双 MATCH+LIKE 扫描 | 独立小批 |
| `task #n` refs 无 UI 消费面 | refs 落库但引用消息不渲染任务 chip | 顺手评估 |
| P11/P3 看板双实现抽 `<TaskBoard>` | blocked 徽标已同构两份，抽共享组件可收 | 顺手评估 |
| messages_fts 键于 messages.rowid | VACUUM 会失同步（external-content 结构性约束） | 观察项 |
| OAuth 冷启动复验 | M1 遗留；真双 Agent OAuth refresh 竞争仍依赖既有确定性单测 | 择机 |

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
