# CoAgentia 当前交接（**M6 里程碑收口 = PRD M6 出口达成；M1–M6 全里程碑完成**）

| 项 | 内容 |
| --- | --- |
| 更新 | 2026-07-13，**M6 复审修复批收口（HEAD `d517624`）**：/code-review high 二轮（8 维 finder→按域对抗核实→10 findings 全 CONFIRMED 全修 +8 回归；含 3 项不可恢复 wedge[fail_closed 空成功旁路/merged 上报丢弃/reopen-cleaned 投递死锁]、指纹崩溃族、投递前缀门收窄、raw control 泄漏[Playwright 实测发现]）+ 实机 verify 48/48 修后复跑通过 + 浏览器/Playwright E2E（chat/canvas/kanban/conflict/repair/direct 六面、console 零错误）。上一轮：2026-07-12，M6 里程碑整体收口（`c37a91f`）。本会话（Fable 单窗编排，COLLAB-MODEL v2 全程实盘）完成：M6b 波 4（J10 delta+O9 ∥ B-M6-2 后半，双 Opus 子代理并行）→ 阶段 4 并行审计（5 维 finder→Fable 终裁修 7 含 1 blocking）+ J11 话术定稿 → **J12 实机 verify 48/48 ALL PASS**（=PRD M6 出口，A1–A8 逐条勾销）→ **/code-review high**（8 维 finder→对抗核实→Fable 终裁 13 findings 修 8[3 major]）→ 文档终态同步+任务书归档。 |
| 定位 | **当前唯一有效的交接入口**（README 约定 1/2）：新会话先读本文；历史背景读 [PROJECT-RECORD.md](PROJECT-RECORD.md)（§13=M6a、§14=M6b）；M1–M6 任务书均已移 archive/ |
| 一句话状态 | **M1–M6 全里程碑收口 = PRD M6 出口达成**。IM 基座→任务域→L2 契约→编排画布+gating→护栏（沉默/freshness/HeldDraft）→双 runtime+配置面→模板向导→Project 交付链（worktree/Diff/merge/check/冲突派回）→**Orchestrator 拆解链**（提案/校验/修复循环/草稿确认/落地/delta/O9）全部落地并经实机 verify。**接续 = M7（未立项）**。 |
| 守门终态 | 后端 **955 passed / 4 skipped** · web vitest **359** · pyright **0**（并入 `pnpm typecheck`）· ruff 干净 · `pnpm gen` 确定（golden **58** 判例双跑逐字节）· web build 绿 · m6_verify 48/48 修后复跑 · **工作树干净** |

## 1. 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia`（monorepo：apps/server·web·daemon·mock-server + packages/contracts·contracts-ts·fixtures）；**无 git remote，全部提交仅存本地** |
| 分支 / HEAD | `main`，HEAD = `c37a91f`（M6 收口文档提交），工作树干净 |
| M6 提交链 | M6a：`d564ebf`→`62939f2`→`6f6fc93`→`bc70cd5`(verify 20/20)→`404aaa8`(review 修复)；M6b：`95d190c`(波1 J7+J11 骨架)→`3a78799`(波2 J8)→`832f2dc`(波3 J9 硬关口)→`3d3e12f`(波4 J10∥B-M6-2 后半)→`19fcfb5`(阶段4 审计+J11 定稿)→`818a483`(J12 verify)→`d303475`(code-review 收口)→`c37a91f`+`5165808`(文档)→**`d517624`(复审二轮 10 findings 全修)** |
| 契约版本 | A **v1.0.10** · B **v1.4.3** · C **v1.0**（连续零修订至 M6 收口）· D **v1.0.3** · E **v1.4** · E2 **v1.0.1**。**M6 全程连续第四个里程碑零新增 Agent 工具**。事实源 = `D:\Project4work\Agenthub_7_8\engineering_docs\` 六契约 + `docx_agenthub\CoAgentia-PRD.md` + `orchestrator_docs\Orchestrator任务拆解设计.md`（拆解实现级权威） |
| 建表批次 | 0001 M1 → 0002 M2 → 0003 M3 → 0004 files 索引 → 0005 messages_fts trigram → 0006 M4 held_drafts → 0007 M5 → 0008 M6a（projects+channel_projects+worktrees+tasks 两列）→ **0009 M6b（proposals[同 source 单一非终态部分唯一索引]+agent_role_templates+agents.role_template_key）** |
| 实机证据 | **M6 = [M6-EVIDENCE.md](../verify/M6-EVIDENCE.md)**：真 uvicorn+真 websockets daemon-sim(真 git.py)+真 scratch 仓库 **48/48 ALL PASS**（S1 拆解全链/S2 冲突派回/S3 修复循环/S4 A5 崩溃重放/S5 delta+O9/S6 single_task/S7 直落；A1–A8 逐条勾销）+ [results.json](../verify/M6-VERIFY-results.json) + 2 截图（chat 全流程叙事/canvas 落地 DAG）；脚本 = `scratchpad/m6_verify.py`（可重跑）。历史：[M6A-EVIDENCE.md](../verify/M6A-EVIDENCE.md)（20/20）、M5/M5A/M4B/M4A/M3B/M3A/M2 系列。 |

## 2. 里程碑总览（详情 = PROJECT-RECORD 对应节）

| 里程碑 | 出口 | 收口 |
| --- | --- | --- |
| M1 契约+实现+hardening | 真实两 Agent 对话/文件产出/reminder | `f2c993f`（§2–§5） |
| M2 任务与看板 | 番茄钟全流程（人发任务→Agent 认领交付→done） | `6c12b90`+`cdb27db`（§6） |
| M3a 契约与校验 | L2 契约链路（提交/修订链/request-draft/T7 门/升格） | `d5f092e`（§7） |
| 挂账三批 | 附件卡数据源 / keyset 分页 / pyright 清零 | `58b89b5`/`9331698`/`0b61669`（§8） |
| M3b 画布与 gating | **PRD M3 出口**：画布建图/成环拒/blocked 推导+投递 gating/force-start | `080ed44`（§9） |
| M4a 沉默提醒与循环 Reminder | D5 升级链 + LoopContract（实机 16/16） | `01ff2d1`（§10） |
| M4b freshness 与 HeldDraft | **PRD M4 出口**（实机 38/38） | `1052ee6`（§11） |
| M5a 第二 runtime 与配置面 | Codex 适配器真机 + 通知/cron/技能白名单/P12 | `da6833a`（§12） |
| M5b 模板与向导 | **PRD M5 出口**：工程三角实例化全管道（e2e 12/12） | `bef88eb`（§12） |
| M6a Project 与交付链 | worktree/Diff/merge --no-ff/check/冲突派回/verdict（实机 20/20 + review 10 全修） | `404aaa8`（§13） |
| **M6b Orchestrator 拆解链** | **PRD M6 出口**：拆解→校验→草稿确认→落地→并行交付→合并；冲突派回；修复循环；delta/O9（**实机 48/48** + review 13 修 8） | `d303475`（§14） |

## 3. 系统当前能力面（一览）

- **IM 基座**（M1）：频道/DM/线程/@mention/文件/已读；真 daemon（Claude Code 适配器）；WS 事件驱动无刷新。
- **任务域**（M2）：as_task/convert、claim/assign/状态机（TASK_TRANSITIONS 单源）、看板、搜索、Activity、daemon MCP 六工具、usage 归属。
- **L2 契约**（M3a）：TaskPlan/TaskHandoff 提交与修订链、request-draft S1 直投、T7 门、升格 l1→l2。
- **编排画布**（M3b）：React Flow 画布、写事务防环、基线指纹推进；**blocked 实时推导**（kernel/graph.py 权威 + lib/graph.ts 镜像 + golden 双跑）；**投递层 gating**（唤醒+投递批双面）；force-start。
- **护栏**（M4）：D5 沉默提醒升级链、循环 Reminder 塌缩重排；**freshness 门**+HeldDraft 三键/G4/G5。
- **双 runtime 与配置面**（M5a）：Codex 适配器（CODEX_HOME 隔离/JSON-RPC）、每频道通知 mute 门、cron cadence、技能白名单、频道设置四组。
- **模板与向导**（M5b）：存为模板/工程三角 builtin/实例化事务器（tmpl: 幂等 reserve-before）/向导三步。
- **Project 与交付链**（M6a）：Project CRUD/频道绑定；writes_code 任务激活即幂等派生 worktree（消息注入工作目录）；daemon git.diff+REST Diff 代理；check/merge 系统节点自动触发、DAG 序 `merge --no-ff`、merge_commit 持久、冲突自动建任务派回、仅 failed 可 retry；review_verdict 四值+needs_human @人类；worktree 指令后台通道+reconnect 复验+ensure 失败三次升级。
- **Orchestrator 拆解链**（M6b，本次收口）：
  - **同构校验内核**（J7）：`kernel/decomposition.py` V1–V14 全量收集 + `<control>` 解析 + 指纹（py 权威）↔ `lib/decomposition.ts` 镜像 ↔ `golden/decomposition.json` 54 判例双跑逐字节（纪律 8 第三组）；枚举成员测试全部 `_is_str` 守卫（unhashable 值不崩、双侧一致）。
  - **提案域**（J8）：8 态状态机（`PROPOSAL_TRANSITIONS` 单点 + `_transition` 全面**条件 UPDATE** CAS）；三入口归一（decompose REST/T1 顶级 @Orch/线程 `<control>`）；上下文注入（角色 prompt_sections 从表读 + 成员/Project 清单携 ULID + S1 直投）；修复循环（每 rev 2 轮，错误信封 §6.3 含 hint，第三败 failed @人类）；对话修正 rev+1 + Superseded；对账 #6；24h 提醒纯推导。
  - **草稿确认与落地**（J9）：confirm CAS（expected 三字段→409 STALE_CONFIRM 携最新态）；调整六 op 服务端权威重验；**202 异步增量落地**——执行器步进原子（每步=节点+其全部入边一个 gateway_tx，封「裸系统节点空成功」窗口），账本逐 op 记行、:done 恰一次（done 标记+baseline bump+已落地消息+landed）；merge 系统节点自动追加（deps=writes_code 前沿）+汇总节点条件追加；直落 auto(channel-policy)；fail-closed 独立连接持久；对账 #4；A5 崩溃重放实证。
  - **delta 增量与 O9**（J10）：Agent 在任务线程发 `<control>` decomposition-delta.v1 → 五步校验（自身 schema/base=画布基线[hint 携当前基线值，修复循环一轮自愈]/结构应用含 NODE_ACTIVE/结果图无环+上限/新增节点内形=信封+过滤复用 kernel）；confirm 复用同两端点（**部分接受 removed_ops**→delta_landed_hash，剔除清单进线程；F9 base 过期=409+提案 failed+要求重出）；落地共享步进 runner（remove_edge→remove_node[锁内重验防 claim TOCTOU]→add_node+入边→add_edge，op_id=原始下标）；**落地期系统节点认领抑制**（running 批期间不认领 idle 节点，fail-closed 后不重扫）；**O9 门**：canvas 四结构写端点+patch_node+模板 instantiate 对 Agent 403 rule=O9（人类不受限 C5）。
  - **Orchestrator 角色模板**（J11）：builtin 数据（§13.1 七条+§12 规模表原文+**第 8 条 delta 通道指引**）、启动 upsert、创建预选、NO_ORCHESTRATOR 引导；模板 PATCH/DELETE（builtin 409）。
  - **前端 B-M6-2**：拆解入口+创建引导链/提案卡（delta 卡读 operations 统计）/草稿层 overlay+确认条防呆（TS 镜像实时校验/CAS/409 latest 刷新/拒绝弹窗）/delta 面板（绿红高亮/逐 op 剔除实时重验[含 running 系统节点 NODE_ACTIVE]/base 横幅）/rev 替换/P12 编排组/wsBridge draft.*·delta.*·landing.* + LandingToaster。

## 4. 接续 = M7（未立项）

**CoAgentia 全部规划里程碑（M1–M6）已完成。** M7 候选面（历史挂账+非目标清单，立项时 owner 拍板）：O8 汇总执行期护栏（stall/replan/轮数上限）、FR-11 预览/FR-12 部署链、递归拆解、拆解质量回路（proposal_hash vs landed_hash 差距回流）、worktree 定向 check/行级 Diff 评论、多机/多用户化（跨进程双直落批等单进程假设的解除）。立项流程照 M5/M6 先例：**契约修订落笔先行（纪律 1）→ 任务书两块竖切 → DEV-PLAN 波次表**；协作模式复审 [COLLAB-MODEL.md](COLLAB-MODEL.md)（v2 Fable 单窗编排在 M6b 全程实盘有效）。

## 5. 挂账（非阻塞，勿当漏项重新发明；全量见 archive/M6-HANDOFF §8）

| 项 | 说明 | 归属 |
| --- | --- | --- |
| **M6b CR-9** | `_post_landed_message` 逐节点 fetch_task/_member_name（N+1，节点数小） | 性能小批 |
| **M6b CR-10** | proposals 部分唯一索引 sqlite_where 谓词字面量与 ProposalStatus 终态集双源 | 观察项（改终态集须同步；可加断言测试） |
| **M6b CR-11** | 0009 downgrade batch recreate agents 时 agent_skills FK 处置未测 | 择机（downgrade 罕跑） |
| **M6b 审计登记** | 跨进程双直落批（`landing_batches` 无 (kind,source_ref) 唯一约束）——单进程 `_landing_lock` 串行化安全，多进程非部署形态 | M7 多机化时收 |
| **M6b 审计登记** | 注入面 prompt 中和（线程摘要 verbatim 进 Orchestrator 注入体，可被伪 `[system` 操纵——单工作区信任模型内；direct 频道无人类闸放大） | 观察项（多用户化前收） |
| **M6b 审计登记** | revalidateDelta 客户端为服务端子集（防呆纵深）/ TS applyAdjustments 潜伏守卫（UI 不可达）/ graph.ts UTF-16 码元序（ASCII 值域无差,可复用 fingerprint.ts cmpCodepoint）/ activeDraft·activeDelta 终态悬挂（良性提示条）/ LandingToaster 不分频道 / P12 越界静默 | 前端低危观察项 |
| M5b 承接 | briefing @全部映射 agent（by-design）/ `_layout_positions` 双实现（尺度不同）/ serialize N+1 | 已接受/观察/性能小批 |
| M4–M2 承接 | held 卡倒计时边角 / hub usage.batch N+1+search 双扫 / `task #n` refs UI 消费面 / `<TaskBoard>` 抽取 / messages_fts rowid（VACUUM）/ OAuth 冷启动复验 | 观察项/性能小批/择机 |
| 模板域 | 模板不携 system 节点 / 实例化不提供 Project 跨频道重映射 | MVP 观察项 |

## 6. 启动方式

**真实开发**：终端 1 `uv run coagentia-server`（8787）；终端 2 `pnpm --filter @coagentia/web dev`（5173，代理 /api→8787）。
**同源构建**：`pnpm --filter @coagentia/web build` 后 `uv run coagentia-server`，开 `http://127.0.0.1:8787`（自动发现 apps/web/dist；异地设 `COAGENTIA_WEB_DIST`）。
**Mock**（显式开启才用）：`VITE_API_BASE=http://127.0.0.1:8642` + `VITE_MOCK_MODE=true` + mock-server。
**隔离实机 verify 范式**：`scratchpad/m6_verify.py`（M6 全场景 48 探针，`--keep` 保活可接浏览器截图）；基建 = `m6a_harness.py`+`m6a_appfactory.py`（临时库 alembic head+seed/真 daemon-sim/独立端口/taskkill 杀树）。

## 7. 守门命令（全绿才算收口）

```
uv run pytest -q                    # 当前 947 passed / 4 skipped（M6 起点 712/4），只增不减
pnpm -F @coagentia/web test         # 当前 vitest 354（M6 起点 175），只增不减
pnpm typecheck                      # 含 pyright（0 错，新债即红）+ 双 tsc
uv run ruff check .
pnpm gen                            # 后 git diff 应为空（生成物确定性）
pnpm -F @coagentia/web build
```

## 8. 注意事项（接手必读）

- **无 git remote**：所有提交仅在本地 main；备份/协作须先 `git remote add` 并 push（owner 决定）。
- **环境**：SQLite ≥ 3.35（RETURNING）；win32 真 claude CLI 踩坑（stream-json `--verbose` 必需/排空 stderr/taskkill /F /T 杀树/git stdout 显式 UTF-8）见 PROJECT-RECORD 与 `scratchpad/GIT-CALIBRATION.md`。
- **迁移纪律**：新迁移按批次显式点名建表（勿 metadata.create_all 全集）；既有表加索引/约束须 `if_not_exists`。
- **纪律 8（同构内核三组）**：graph / fingerprint / **decomposition**——改语义必须 py 权威+ts 镜像+golden 三处同步，双跑逐字节守门；内核枚举成员测试必须 `_is_str` 守卫（unhashable 防崩 + 双侧一致）。
- **CAS 纪律（M6 最重要教训，三度印证）**：pysqlite 方言 SELECT 在首个 DML 前跑自动提交（无快照）——「同 tx 读=串行化点」不成立。**凡状态机边写必条件 UPDATE**（WHERE status=起态，竞败 rowcount=0→StaleTransition）；凡 read-then-act（如 remove_node 活动复核）须把复核移到写锁之后（先写取锁→锁内重读→不符回滚）。
- **落地期系统节点抑制**：running 落地批期间不认领 idle merge/check（`_channel_landing_in_progress`）；**fail-closed 后不自动重扫**（截断前缀图上 merge 会空成功进不可 retry 终态）——只 LANDING_COMPLETED 触发补扫描。
- **O9 面清单**：canvas create_node/delete_node/create_edge/delete_edge/patch_node + templates instantiate 对 Agent 403 rule=O9；Agent 结构变更唯一通道 = `<control>` full/delta 提案；人类不受限（C5）。新增任何「落画布结构」端点须同口径。
- **gating 语义**（M3b 教训）：作用于投递层 = 唤醒触发 **和** 投递批双面。
- **幂等身份纪律**（M5b 教训）：落地批 req_hash 折入 source 身份；重放按构造序（ledger seq），勿按字典序。
- 实机验证起的 server/浏览器/daemon-sim 进程结束前必杀（taskkill /F /T）。
