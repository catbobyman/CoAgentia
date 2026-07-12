# CoAgentia 当前交接（M5 收口 · **M6a 收口完成：verify 20/20 + code-review 10 findings 全修复验**）

| 项 | 内容 |
| --- | --- |
| 更新 | 2026-07-12，**T1（M6a 收口段）完成**：并行审计（8 角度 workflow）→ `/code-review high` 10 findings（1 REFUTED）→ 实机 verify 20/20（`bc70cd5`）→ **10 findings 统一修复 + 14 项回归测试 + 全守门复验 + m6a_verify 复验 20/20 → 收口提交（见 HEAD）**。修复由 Codex 起草、Fable 5 接手对抗审查（10 条全过）+ 修 5 红测试 + 补回归 + 收口。守门新基线 **827/195/pyright0**。 |
| 定位 | **当前唯一有效的交接入口**（README 约定 1/2）：新会话先读本文；历史背景读 PROJECT-RECORD；M5 任务书已移 archive/ |
| 一句话状态 | **M1–M5 全收口；M6a 全收口（实现+实机 verify 20/20+code-review 10 findings 全修）**。**M6b 进行中（COLLAB-MODEL v2 Fable 单窗编排）**：波 1（J7 ✅，`95d190c`）→ 波 2（J8 ✅，`3a78799`）→ **波 3（J9 ✅ 硬关口通过[Fable 重写 2 blocking：步进原子落地/条件转移] + B-M6-2 前半 ◐）已收口提交（见 HEAD）**；契约 A **v1.0.10** 已落 0009；**接续 = 阶段 3（波 4）：J10 delta+O9 ∥ B-M6-2 后半（owner 指定新窗口执行，入场读本文 + M6-DEV-PLAN 波 4 行 + J9 行架构注记）**。 |
| 提交链（M6a） | `d564ebf` 波 1（J3-cal/J0/J1）→ `62939f2` 波 2（J2/J3+补遗）→ `6f6fc93` 波 3（J4/J5/J6/B-M6-1）→ `bc70cd5` 实机 verify+findings 登记 → **code-review 修复收口（见 HEAD）**；中间 `1633ad9` 是独立协作规程文档提交 |
| 提交链（M5b） | `b4203c4` 波1(H5+B-M5-2) → `12aaac6` 波2(H6) → `42b7b64` 审计修复 → `bb760f0` H7 verify+verify-surfaced 修复 → `bef88eb` code-review 修复 |

## 1. 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia`（monorepo：apps/server·web·daemon·mock-server + packages/contracts·contracts-ts·fixtures）；**无 git remote，全部提交仅存本地** |
| 分支 / HEAD | `main`；本交接随 M6a code-review 修复收口提交（见 HEAD），前序 HEAD=`bc70cd5` |
| 提交链（M6a） | `d564ebf` → `62939f2` → `6f6fc93` → `bc70cd5` → 修复收口（见 HEAD）；波 2 后另含 `1633ad9` 文档提交 |
| 提交链（M5b） | `b4203c4` 波1(H5+B-M5-2) → `12aaac6` 波2(H6) → `42b7b64` 并行审计修复 → `bb760f0` H7 verify+verify-surfaced 修复 → `bef88eb` code-review 修复 |
| 测试基线 | 后端 **915 passed / 4 skipped**（`uv run pytest -q`，M6b 波 3 起）· web vitest **296** · pyright **0**（并入 `pnpm typecheck`）· ruff 干净（波 1 顺修 M6a 遗留 scratchpad 4 条 lint）· `pnpm gen` 确定 · web build 绿 |
| 契约版本 | A **v1.0.10**（M6b 开工补遗：0009 批次=proposals+agent_role_templates 两张+agents.role_template_key 列，owner 拍板 2026-07-12「方案 A」）· B **v1.4.3** · C **v1.0**（连续零修订至 M6）· D **v1.0.3** · E **v1.4** · **E2 v1.0.1**——B v1.4.2 收 Computer→Project FK 删除门；A v1.0.9/B v1.4.3 收 TemplateNode 与模板实例化交付字段，均为 v1.0.7 连带补遗、非产品意图变更。事实源 = `D:\Project4work\Agenthub_7_8\engineering_docs\` 六契约 + `docx_agenthub\CoAgentia-PRD.md` + `orchestrator_docs\Orchestrator任务拆解设计.md`（拆解实现级权威） |
| 建表批次 | 0001 M1 → 0002 M2 → 0003 M3 → 0004 files 索引 → 0005 messages_fts trigram → 0006 M4 held_drafts → 0007 M5 → **0008 M6a（projects + channel_projects + worktrees + tasks 两列）** → **0009 M6b（proposals + agent_role_templates + agents.role_template_key 列，A v1.0.10）** |
| 实机证据 | **M6a = [M6A-EVIDENCE.md](../verify/M6A-EVIDENCE.md)**（真 uvicorn+真 websockets daemon-sim(真 git.py)+真 scratch 仓库 20/20，`bc70cd5` 收口；修复后复验 20/20）。[Project 设置](../verify/m6a-project-settings.png)、[Diff/verdict](../verify/m6a-diff-verdict.png)、[系统节点](../verify/m6a-system-nodes.png) 是 B-M6-1 屏对照夹具。历史：[M5-EVIDENCE.md](../verify/M5-EVIDENCE.md) 与 [M5A-EVIDENCE.md](../verify/M5A-EVIDENCE.md)。 |

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
| **M6a Project 与交付链** | Project/worktree/Diff/check/merge/冲突派回/verdict/B-M6-1 + 实机 verify 20/20 + code-review 10 findings 全修 | `d564ebf` → `62939f2` → `6f6fc93` → `bc70cd5` → 修复收口（见 HEAD，§4a） |

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
- **M6a Project 与交付链（波 1–3 实现完成）**：Project CRUD/频道绑定与 `channel_ids` 派生；Computer 删除按 Agent→Project 固定门序；writes_code 任务激活后幂等派生短路径 worktree，状态回流、对账 #5、keep_days 清理、绝对路径消息注入及模板字段贯通；daemon `git.diff` 与 REST Diff 代理；check/merge 系统节点自动触发、仅 failed retry、DAG 序 `merge --no-ff`、成功持久 `merge_commit`、冲突自动建任务派回；review_verdict 四值与 needs_human @人类；前端 Project/Diff/系统节点/verdict/冲突卡及 `worktree.updated` 实时面。真机全链尚待 verify。

## 4. 接续 = M6b（**M6a 全收口；M6b 未开工**）

**任务书 = [M6-HANDOFF.md](M6-HANDOFF.md)**。块 a 全链完成：波 1 `d564ebf` → 波 2 `62939f2` → 波 3 `6f6fc93` → 实机 verify `bc70cd5` → **code-review 10 findings 统一修复收口（见 HEAD）**，§9a 出口清单全勾。接续 = 块 b（J7→J12+B-M6-2），入场提示词 = [COLLAB-MODEL §4a](COLLAB-MODEL.md)（Opus 4.8 执行主力，J9 交 Fable 对抗审查）。拆解流程的实现级权威 = `orchestrator_docs/Orchestrator任务拆解设计.md`，**开工前勿动 orchestration/、proposals 或 0009**。

## 4a. M6a `/code-review high` findings（10 条，**已全部修复 + 回归复验**，收口提交见 HEAD）

8 角度 workflow → 10 findings（1 REFUTED）→ owner 拍板（#1 后台化 Design C / #7 保语义删死代码 / 其余全修）→ 修复由 Codex 起草、Fable 5 对抗审查通过后收口。修复摘要：

| # | 判 | 修复落地 | 回归 |
| --- | --- | --- | --- |
| 1 | CONFIRMED | daemon `client.py`：worktree ensure/merge/cleanup 走后台通道（`_BACKGROUND_INSTRS` 分流 + 单车道 `_worktree_lane` 串行防同仓并发 git）；**ack 仍在 op 完成后发**（status→ack 保序）、异常仍 ack FAILED、**断连不取消在飞任务仅 shutdown 取消**；handlers.py 零改动 | test_worktree_handlers 6 项（reader 不阻塞/硬失败/shutdown 取消等） |
| 2 | CONFIRMED | hub `_record_worktree_failure`：诊断归属 owner/channel/task + `.returning(seq)` + DIAGNOSTIC_APPENDED 广播；ensure **累计失败 ==3 一次性升级**（频道系统消息+fail_closed activity 喊频道人类） | 归属+WS 断言、3 次升级一次性（含升级消息再扫描交互）、无 owner/cleanup 不升级 |
| 3 | CONFIRMED | `worktrees/service.revalidation_plans`（仅 active×未终态，**conflicted 排除**防冲突态被覆盖回 active）+ hub `_ensure_worktree(revalidate=)` + `reconcile(revalidate_worktrees=True)` 仅 reconnect 握手传入 | reconnect 下发/周期不下发/conflicted 不复验/plans 单测；测试桩 `drain_revalidation` 辅助 |
| 4 | CONFIRMED | `_create_conflict_task` 顶部幂等查：本 merge 节点已有未终态同树派回任务则复用；二次真冲突（前次 done）建新 | 重复上报不重建/二次真冲突建新 |
| 5 | CONFIRMED | hub `GitQueryError`（**不继承 DaemonOffline**）→ routes 422 VALIDATION_FAILED 透传 git prose；DaemonOffline 收敛纯 503 | test_diff_query_failure_is_validation_error |
| 6 | CONFIRMED | 契约 `CardKind.MERGE_CONFLICT` + `pnpm gen` + `post_system_message(card_kind=)` + 冲突 anchor 落 card_kind + 前端按 `m.card_kind==='merge_conflict'` 判定 | 后端 anchor card_kind 断言 + 前端 fixture + 假冲突卡负例 |
| 7 | 拍板保留 | 连续前缀投递语义不变（防丢消息权衡）；删死代码 `_filter_gated`，rationale 折进 `_filter_agent_delivery` docstring | 无（死代码删除） |
| 8 | CONFIRMED(效率) | daemon `git.py::diff` 一次全量 diff + `_split_diff_sections` 按行首 `diff --git ` 切分 + 段数不符 fail-closed | 进程数恒定计数/切分单测/内容行含 diff 头不串段 |
| 9 | PLAUSIBLE | `apply_merge_result` 传 `reconciled` set：alias 更新+WORKTREE_UPDATED 按 worktree_row.id 整轮去重；进展消息 per-node 保留 | 菱形拓扑广播一次+两条进展消息 |
| 10 | PLAUSIBLE | merged 缺 merge_commit 分支加 fail-closed 注释（空 merge_node_ids=迟到/重复报静默丢弃不 wedge，行终态另有可达路径） | 仅注释 |

**未入榜（已登记）**：`buffer.py` 缺父目录 fsync（仅 POSIX，win32 无碍） / `_STATUS_REPLAY_INSTRS` 白名单现仅 CHECK_RUN（当前正确，潜在脆弱） / hub 未迁移到共享 `messages/service.py` 且已丢 mention 去重（潜在，复合 PK 会先报错非渲染重复） / 投递热路径若干 N+1。**收口注**：`messages.card_kind` 为 TEXT+CHECK（native_enum=False），fresh `alembic upgrade head` 自带新值；已有持久生产库会拒 `merge_conflict`——本仓无 remote/无持久生产库，可接受。m6a_verify 复验 4 轮 3 净 1 环境性 REST 超时（probe 既有「残留 DB 锁」重试脚手架佐证为环境噪声，失败窗口无本轮新代码参与）。

**verify-surfaced 补充观察**（M6A-EVIDENCE §4）：**裸系统节点空成功**——per-node REST 建 merge/check 节点时 `CANVAS_NODE_ADDED` 即触发系统节点扫描，此刻无上游边 → 非 blocked → 空 steps `_succeed_merge_node`；真实产品随 landing 批节点+边同事务，但手工建节点+补边路径存此窗口，建议随 M6b 提案落地复核。

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
| held 系统消息骨架 / human_members DRY | J6 已抽中立 `messages/service.py` 供 guard 与 needs_human 共用；hub 内局部查询仍是性能/清理观察项，held 消息骨架未扩界 | 部分收敛/观察项 |
| hub usage.batch N+1 / search 双扫 | 承接 M2 挂账（性能小批） | 独立小批 |
| held 卡「重评估中…」显示边角 | 升级态 held 行倒计时显示（正常 G4 翻转才准） | 观察项 |
| `task #n` refs 无 UI 消费面 / P11·P3 看板双实现抽 `<TaskBoard>` | M2 观察 | 顺手评估 |
| messages_fts 键于 rowid（VACUUM 失同步）/ OAuth 冷启动复验 | M3/M1 结构性观察 | 观察项/择机 |
| 模板携带 system 节点 | M6a MVP 模板仍仅承载 task；merge/check 由实例化后在画布手动添加 | 观察项 |
| 模板实例化 Project 重映射 | MVP 固定复核原 project_id 已绑定目标频道，不提供跨频道重映射面 | 观察项 |

## 6. 启动方式

**真实开发**：终端 1 `uv run coagentia-server`（8787）；终端 2 `pnpm --filter @coagentia/web dev`（5173，代理 /api→8787）。
**同源构建**：`pnpm --filter @coagentia/web build` 后 `uv run coagentia-server`，开 `http://127.0.0.1:8787`（自动发现 apps/web/dist；异地部署设 `COAGENTIA_WEB_DIST`）。
**Mock**（显式开启才用）：`VITE_API_BASE=http://127.0.0.1:8642` + `VITE_MOCK_MODE=true` + mock-server。
**隔离实机 verify 范式**：临时库 `COAGENTIA_ALEMBIC_URL` alembic head + seed + 注入测试 key + 独立端口（8799 先例）；参照 M3B-EVIDENCE 与 scratchpad launcher 脚本体例。

## 7. 守门命令（全绿才算收口）

```
uv run pytest -q                    # 当前 813 passed / 4 skipped；M6 起点 712/4
pnpm -F @coagentia/web test         # 当前 vitest 194；M6 起点 175
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
- **M6a 波 3 剩余风险（非当前阻断）**：TerminateProcess/断电仍可能越过进程内 merge/check 恢复窗口，尚无持久 in-flight journal/Job Object；未做完整 server+Hub 销毁重建的重启测试；跨物理机器迁移 Project 的旧树清理不在 Windows 单机 MVP 范围。留给真机 verify/code-review 继续观察，不扩契约或 schema。
- 实机验证起的 server/浏览器/daemon-sim 进程结束前应关闭（8799 等端口）。
