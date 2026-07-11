# CoAgentia 当前交接（M5 收口 · **M6a 波 1 施工中**）

| 项 | 内容 |
| --- | --- |
| 更新 | 2026-07-11，**M6a 波 1 守门全绿，待本地提交**：J3-cal ✅（Windows scratch git 10/10）→ J0 ✅（contracts/mock/conformance）→ J1 ✅（0008：三表含 merge_commit + tasks 两列；从零与历史 M5 schema 切片增量双路绿）。守门 = pytest **724 passed / 4 skipped**、web **175**、typecheck/pyright 0、ruff、gen 确定、web build 绿。开工补遗 A **v1.0.8** / B **v1.4.1** 已同步；D v1.0.3 不变。 |
| 定位 | **当前唯一有效的交接入口**（README 约定 1/2）：新会话先读本文；历史背景读 PROJECT-RECORD；M5 任务书已移 archive/ |
| 一句话状态 | **M1–M5 全收口；M6a 波 1 已绿，提交后进入波 2**。J0 focused 135 绿且 gen 二次确定；J1 migration 17 绿，0008 从零与历史 M5 schema 切片增量升级都保留既有任务并补默认 writes_code=0。J2–J6/B-M6-1/实机 verify 尚未开始。 |
| 提交链（M5b） | `b4203c4` 波1(H5+B-M5-2) → `12aaac6` 波2(H6) → `42b7b64` 审计修复 → `bb760f0` H7 verify+verify-surfaced 修复 → `bef88eb` code-review 修复 |

## 1. 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia`（monorepo：apps/server·web·daemon·mock-server + packages/contracts·contracts-ts·fixtures）；**无 git remote，全部提交仅存本地** |
| 分支 / HEAD | `main` / `eca70e6 M6 kickoff docs`；M6a 波 1 待提交 |
| 提交链（M5b） | `b4203c4` 波1(H5+B-M5-2) → `12aaac6` 波2(H6) → `42b7b64` 并行审计修复 → `bb760f0` H7 verify+verify-surfaced 修复 → `bef88eb` code-review 修复 |
| 测试基线 | 后端 **724 passed / 4 skipped**（`uv run pytest -q`）· web vitest **175** · pyright **0**（并入 `pnpm typecheck`）· ruff 干净 · `pnpm gen` 确定 · web build 绿 |
| 契约版本 | A **v1.0.8** · B **v1.4.1** · C **v1.0**（连续零修订至 M6）· D **v1.0.3** · E **v1.4** · **E2 v1.0.1**——v1.0.8/v1.4.1 是 M6 开工补遗：worktrees.merge_commit 持久落点、ProjectPublic.channel_ids 与 Project 请求/响应精确形状；contracts 包已由 J0 同步；事实源 = `D:\Project4work\Agenthub_7_8\engineering_docs\` 六契约 + `docx_agenthub\CoAgentia-PRD.md` + `orchestrator_docs\Orchestrator任务拆解设计.md`（拆解实现级权威） |
| 建表批次 | 0001 M1 → 0002 M2 → 0003 M3 → 0004 files 索引 → 0005 messages_fts trigram → 0006 M4 held_drafts → **0007 M5（templates + channel_notification_settings）**；**templates 表 M5b 已全 serve**（存为模板/列表/实例化落地批消费），channel_notification_settings 已全 serve |
| 实机证据 | [M5-EVIDENCE.md](../verify/M5-EVIDENCE.md)（**M5b/M5 出口**：codex 真机 PONG / e2e 12/12 全管道到人类终审 done / 5 前端截图 / console 0 / §5b code-review）· [M5A-EVIDENCE.md](../verify/M5A-EVIDENCE.md)（M5a）；此前各批同目录 |

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
| **M5a 第二 runtime 与配置面** | Codex 适配器真机对话跑通 + 通知设置 mute 门 + cron cadence + 技能白名单 UI + P12 阈值（REST 9/9 + codex PONG） | **`da6833a`** |
| **M5b 模板与向导** | **PRD M5 出口**：工程三角向导实例化（实现=Codex、评审=Claude）→ briefing 开工 → 全管道到人类终审 done（e2e 12/12 + 5 截图 + code-review 6 CONFIRMED 全修） | **`bef88eb`（本文）** |

## 3. 系统当前能力面（一览）

- **IM 基座**（M1）：频道/DM/线程/@mention/文件/已读；真 daemon（Claude Code 适配器）双 Agent 对话；WS 事件驱动无刷新。
- **任务域**（M2）：as_task/convert 建任务、claim/assign/状态机（TASK_TRANSITIONS 单一事实源）、看板 P3/P11、搜索三分组、Activity、daemon MCP 六工具、usage 归属。
- **L2 契约**（M3a）：TaskPlan/TaskHandoff 提交与修订链、Agent 起草 request-draft S1 直投、T7 流转门（l2→in_review 校验 handoff）、升格 PATCH level l1→l2。
- **编排画布**（M3b）：每频道画布页签（React Flow）——节点=任务（agent 节点=第三创建途径，建 L2+锚点消息）、边=依赖（写事务拓扑排序防环）、基线快照指纹推进；**blocked 实时推导**（`kernel/graph.py` 权威 + 前端 `lib/graph.ts` 镜像，`golden/graph.json` 双跑对照）；**投递层 gating**（blocked 任务线程消息不唤醒、不入投递批、read_position 水位不越过）；**force-start**（仅人类、双留痕、不改状态、本次放行）；看板 blocked 徽标。
- **中文检索**（浮动件）：messages_fts trigram（≥3 字 MATCH + <3 字 LIKE 兜底，元字符转义）。
- **护栏与提醒**（M4a）：**D5 沉默提醒升级链**（tasks/silence.py 防自激 last_activity + hub 后台扫描：三态阈值提醒 Todo→创建者/InProg→owner/InReview→频道人类 → 升级主流消息 + activity silence_escalation → 升级后静默；task_events 纯推导无状态列）；**循环 Reminder**（create_reminder 内联 LoopContract 建即生效 + task_contracts 挂接行 + `interval.next_after` 塌缩式重排防重放风暴）；前端 P6 Reminders 页签 + Activity 置顶。
- **freshness 护栏与 HeldDraft**（M4b）：**freshness 门**（guard/service.py 判定单源——scope=线程/主流未读、仅 Agent 主体过门、幂等 hit 优先于门；扣草稿建/刷新 held 单活动行 SAVEPOINT 兜并发再扣，202 不落库）；**三键人类干预** / **G4 超时自愈** / **G5 升级喊人** / GC 豁免活动 held 附件；前端 HeldDraftCard。
- **第二 runtime 与配置面**（M5a）：**Codex 适配器**（`adapters/codex.py` CodexProcess 驱动 codex app-server 长驻 JSON-RPC；`claude_code.py::_new_process` 按 `boot.runtime` 分派、管理器 runtime 无关共用 on_exit 熔断；CODEX_HOME 隔离 + config.toml 注入 MCP + auth.json mtime 新鲜度物化；`probe_codex` 冷探 model/list+skills/list；护栏/任务/契约/usage 对 codex 全语义生效）；**每频道通知设置**（`GET/PUT /channels/{id}/notification-setting` 人类自治/Agent 403/dm 422/**原子 upsert**；mute 门单源 `activity/service.muted_members`，dm/held_escalation 不过门=必达；ChannelsSnapshot 扩字段）；**cron cadence**（`reminders/cron.py`+`cadence.py` 手写 5 段无依赖、Vixie 日∨周并集、validate 可满足性探测拒 impossible、next_after UTC 严格比较兜 DST fold、8 年上限 + 塌缩 next-fire）；**技能白名单 UI**（候选池 = `detected_runtimes[].skills`，claude 扫 `~/.claude/skills/`(跳 symlink)、codex 走 skills/list——两 runtime 均有池）；**频道设置弹窗四组**（P12 阈值收编）+ cron 人读预览 + 通知徽标。
- **模板与向导**（M5b）：**模板域**（`templates/service.py` 存为模板读画布快照序列化 TemplateBody[仅 task 节点/占位 owner 去重/plan_skeleton 带走/pos 不入/node key `n{idx}`]、`validate_template_body` 单执法点[无环+引用一致]、列表 builtin 置前、`upsert_builtin_templates` 启动幂等；**工程三角 builtin** = `templates/builtin.py` 6 节点线性 DAG[需求框定→评审门→实现契约→TDD 实现→独立验收→人类终审] + 4 角色占位[checker≠doer 话术] + briefing + 每节点 plan_skeleton）；**实例化事务器**（`routes/templates.py` POST instantiate：role_mapping 全覆盖 422 + 未知成员 422 + 无画布 404 全前置于**幂等 reserve-before**[record 先于副作用、req_hash 折 template_id、并发同键不重复落地批]；`templates/service.instantiate_template` 单事务：落地批 kind=tmpl → 逐节点 create_node 全链[`tmpl:<batch_id>:<node_key>` 幂等/分层布局 `_layout_positions`] → 连边[无环兜底+triplet SAVEPOINT] → briefing @映射角色[唤醒] → baseline bump → mark_done；重放 reconstruct 由 `ledger.batch_node_task_ids` 按 seq 保序派生；**blocked-gating 天然生效**——落地边即入 derive_blocked）；**向导三步 B-M5-2**（选模板[卡+DAG 缩略图]→角色映射[同 runtime 互审 warning=`lib/templates.classifyRole` **仅按占位名判定**]→预览→实例化跳画布；`SetupChecklist 003` 接真；幂等键每次提交作废重置 + `crypto.randomUUID` 兜底）。

## 4. 接续 = M6（**已立项 2026-07-11，待开工**）

**任务书 = [M6-HANDOFF.md](M6-HANDOFF.md)**。块 a 波 1 当前进度：**J3-cal/J0/J1 ✅，全守门中**；通过后单次本地提交并进入波 2（J2 Project 域 ∥ J3 worktree 生命周期）。拆解流程的实现级权威 = `orchestrator_docs/Orchestrator任务拆解设计.md`（M6b 尚未开工，勿动 orchestration/）。

## 5. M5 挂账（非阻塞，勿当漏项重新发明）

| 项 | 说明 | 归属 |
| --- | --- | --- |
| **M5b** briefing @全部映射 agent | 含下游 blocked 任务 owner——by-design 唤醒信号；gating 仍护任务线程投递，非有害绕过 | 已接受（code-review PLAUSIBLE） |
| **M5b** `_layout_positions` 与前端 `TemplateDagThumb` 分层重复 | 两处 Kahn 分层（尺度/用途不同：server 真坐标 vs 前端缩略图），非纪律 8 图算法单源 | 观察项 |
| **M5b** serialize `_plan_skeleton` N+1 | 存为模板逐 task 节点 active_contract SELECT（稀有路径、节点数小） | 性能小批 |
| **M5b** 模板 DELETE/PATCH 端点缺 | 列表污染治理（单人类可接受） | M6+ |
| **M5b** fail-closed 持久性（node-mismatch 分支） | 该分支 M6-only 不可达（fresh batch_id）；ApiError 回滚普通写，mark_fail_closed 若走普通写则不持久，M6 replay 接真前须复核 | M6 前复核 |
| 凭证物化目录权限 | codex CODEX_HOME 父目录未 chmod 0700（NFR5 单机单用户信任模型内，多用户非目标 PRD §9） | 已接受 |
| cron 描述文案双处 | `daemon/mcp.py` 与 server 校验两处 cron 描述，未来语法扩展易漂移 | 顺手小批 |
| ChannelsSnapshot 通知行无分页 | 本人非默认通知行全量返回（单人类频道数小） | 观察项 |
| held 系统消息骨架 / human_members DRY | 承接 M4b #8/#9（非 bug） | 顺手小批 |
| hub usage.batch N+1 / search 双扫 | 承接 M2 挂账（性能小批） | 独立小批 |
| held 系统消息骨架 / human_members 跨模块重复（M4b 评审 #8/#9） | DRY 债非 bug，消除需抽中立共享模块触及承重 reminder/silence 路径 | 顺手小批 |
| held 卡「重评估中…」显示边角 | 升级态 held 行倒计时显示（正常 G4 翻转才准） | 观察项 |
| `task #n` refs 无 UI 消费面 / P11·P3 看板双实现抽 `<TaskBoard>` | M2 观察 | 顺手评估 |
| messages_fts 键于 rowid（VACUUM 失同步）/ OAuth 冷启动复验 | M3/M1 结构性观察 | 观察项/择机 |

## 6. 启动方式

**真实开发**：终端 1 `uv run coagentia-server`（8787）；终端 2 `pnpm --filter @coagentia/web dev`（5173，代理 /api→8787）。
**同源构建**：`pnpm --filter @coagentia/web build` 后 `uv run coagentia-server`，开 `http://127.0.0.1:8787`（自动发现 apps/web/dist；异地部署设 `COAGENTIA_WEB_DIST`）。
**Mock**（显式开启才用）：`VITE_API_BASE=http://127.0.0.1:8642` + `VITE_MOCK_MODE=true` + mock-server。
**隔离实机 verify 范式**：临时库 `COAGENTIA_ALEMBIC_URL` alembic head + seed + 注入测试 key + 独立端口（8799 先例）；参照 M3B-EVIDENCE 与 scratchpad launcher 脚本体例。

## 7. 守门命令（全绿才算收口）

```
uv run pytest -q                    # 712 passed / 4 skipped 基线，零回归
pnpm -F @coagentia/web test         # vitest 175 基线，只增不减
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
