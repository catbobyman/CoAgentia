# CoAgentia 当前交接（**MVP M1–M7 完成；M8 进行中——块 M8a「加固批」+ 块 M8b「O8 编排质量线」已收口；接续 = 块 M8c**）

| 项 | 内容 |
| --- | --- |
| 更新 | **2026-07-14，块 M8a「加固批」收口**（`ab84406`；M8 三块之首，M8b/M8c 未开工）。本会话（Opus 单窗编排，一路径直做 + 前端派 sonnet 子代理并行）完成：**L0** 契约落笔 A v1.0.12（summary_runs + canvas_nodes.upstream_policy）/ B v1.5.1（NodeCreate.upstream_node_ids + replan 403 rule=O8 + §2 补登 O9/O8）+ **迁移 0012 前移随 L0 落地**（`3532b6a`）→ **L1** 手动系统节点原子建边（POST /nodes 消费 upstream_node_ids 同 tx 建节点+入边，封 K1 空成功竞态，`3f002fd`）→ **L4** CR-M8-1 同族残留收敛（**L4a 读循环收帧入队+独立 writer 消费**[DB 写 offload to_thread、不阻塞 loop/不撞锁撕连接、_spawn 线程安全、_system_pending 加锁] / **L4b** discard 预检+inject after_commit / **L4c** reevaluate 已提交后钉住，`4cd79a8`）→ **B-M8-1** 前端①②③④（上游多选/深链纠偏/预览遮挡 z-index/R-13 触发者，`b8f9e64`）→ **L6** 部署日志残窗 R-10 游标 sidecar + R-14/⑤ 前端 chunk_seq 拼接去重（`ab84406`）。**以下为上一里程碑 M7：** 2026-07-13，**M7 里程碑整体收口 = MVP 全部规划里程碑（M1–M7）完成**（`b18adbe`）。本会话（Fable 单窗编排，COLLAB-MODEL v2 全程实盘，workflow 编排多 Opus 子代理执行/评审）完成**块 M7b「部署、成本与收尾」**：K4 部署域全链（0011 迁移/routes/deployments.py R8·409·Idempotency·主干 HEAD 快照·token_summary 新账/hub deploy.log·deploy.finished CAS·结果卡多频道·**对账 #10 fail-closed**/daemon DeployRunner 流式+末 URL+30min 杀树，`8d26abe`）→ 波2 K5 trigger_deploy 工具 ∥ K6 GET /usage 三层 ∥ B-M7-2 部署卡+成本面（`e06a793`）→ 波3 K7 性能小批四件（语义逐字节等价）∥ K8 预留位审查文档 R-1~R-15（`9082827`）→ **K9 实机 verify 29/29 ALL PASS + 浏览器可视 E2E**（`f6655d1`）→ **/code-review high**（8 维 Opus finder→对抗核实 12 CONFIRMED，Fable 终裁修 4 真缺陷：next_after 空按钮 / deploy.log 提交后落盘防丢帧·重复 / usage level=agent 一致性 / 诊断去重抽单点，`b18adbe`）。上一轮：M7a 预览链收口（`d238988`）、M6 里程碑（`d303475`）。 |
| 定位 | **当前唯一有效的交接入口**（README 约定 1/2）：新会话先读本文；历史背景读 [PROJECT-RECORD.md](PROJECT-RECORD.md)（§13=M6a、§14=M6b、**§15=M7**、**§16=M8b**）。**M8 进行中**：任务书 = [M8-HANDOFF.md](M8-HANDOFF.md)（M8a 加固批 ✅ / M8b O8 编排质量线 ✅ / M8c 单机外壳+体验收官），执行计划与进度 = [M8-DEV-PLAN.md](M8-DEV-PLAN.md)。**接续 = 块 M8c（B-M8-3 外壳 + L10/L11/L12/L13）**：M8c 波序见 M8-DEV-PLAN §1「块 M8c」——外壳件（Members 页建 Agent/首跑清单按钮/新建频道/L11 打招呼）先行，L12 编排体验收官（番茄钟系多节点 decompose 全链真机 + 教程），**L13 m8_verify 收口含 O8 全链真机演示（§9b #11，任务书「可并入 L13」）**。多租户/多机/多副本挂账（[M7-RESERVATION-AUDIT.md](../M7-RESERVATION-AUDIT.md) R-1~R-9/R-11/R-12/R-15；~~R-10/R-13/R-14~~ 已随 M8a 收）维持 M9+ 未立项。 |
| 一句话状态 | **M1–M7 全完成 = MVP 收口**。系统现能：需求消息 → 拆解 → 并行交付 → **Diff/预览验收**（daemon 长驻 dev server / iframe 真机可达 / 并排 / idle 回收）→ 合并 → **一键部署**（人类点击 + Agent `trigger_deploy` 双通道 / 409 串行 / 日志实时流 / 结果卡 URL）→ **成本核算**（GET /usage 三层 / 部署新账 token 小结 / 覆盖率诚实标注永不折算货币）——"从对话到上线"产品承诺闭环。 |
| 守门终态（M8b 收口） | 后端 **1117 passed / 4 skipped**（M8b L7 起点 1089）· web vitest **502**（B-M8-2 起点 472）· pyright **0**（并入 `pnpm typecheck` + 双 tsc）· ruff 干净 · `pnpm gen` 确定（L7 后逐字节稳定，golden 含 partial 判例）· web build 绿 · **工作树干净**。块 M8b 实机 O8 全链 verify 归块 M8c L13（§9b #11「可并入 L13」）——本块以单测 + 探针（护栏三红例/CAS 竞态/F6 崩溃续算 + 前端横幅态机·卡片·改档·wsBridge 失效）守门；B-M8-2 截图对照 = 独立 story 四件真主题渲染经 DOM 核实（截图工具本机超时，get_page_text/read_page 已证）。 |
| M7 里程碑证据 | **[M7-EVIDENCE.md](../verify/M7-EVIDENCE.md)**：块 M7a 14/14 + 块 M7b **29/29 ALL PASS**（真 uvicorn+真 daemon-sim[真 PreviewRunner/真 DeployRunner 起真子进程]+真 scratch git；预览 iframe 200→合并 --no-ff→部署双通道 URL+新账小结→409→GET /usage 三层→对账 #9/#10 崩溃探针）+ [M7B-VERIFY-results.json](../verify/M7B-VERIFY-results.json) + **浏览器可视 E2E** [M7B-BROWSER-E2E.txt](../verify/M7B-BROWSER-E2E.txt)（真前端渲染部署卡 URL·退出码·耗时·新账 Σ1.6k·上报 1/1，Agent 卡 Σ0 新账正确）；脚本 = `scratchpad/m7b_verify.py`（可复跑）。 |

## 1. 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia`（monorepo：apps/server·web·daemon·mock-server + packages/contracts·contracts-ts·fixtures）；**无 git remote，全部提交仅存本地** |
| 分支 / HEAD | `main`，HEAD = `a51637c`（**块 M8b 收口 = B-M8-2 前端**，2026-07-14）+ 本次交接文档同步提交，工作树干净。M8b 提交链：`cb5d00b`(L7 W9 双档内核)→`7d4c910`(L8 O8 汇总执行域)→`872762a`(L9 质量回路+话术)→`e68abcd`(L8 F6 补测)→`62a1e60`(L9 停下交接登记)→**`a51637c`(B-M8-2 O8 前端可见面)**。M8a 提交链：`3532b6a`(L0 契约+0012)→`3f002fd`(L1 原子建边)→`4cd79a8`(L4 读循环并发)→`b8f9e64`(B-M8-1 前端①②③④)→`ab84406`(L6 R-10/R-14)；`ff46db7`(收尾杂项)、`887bd3f`(M8a 块收口文档)。此前 `7320c47`(M8 立项文档)、`d2d4a20`(前端死壳 F1–F13)、`62832f4`(CR-M8 真机双修)、M7 收口 `b18adbe`+`475cb6d`。**CR-M8 双修**（MVP 后首次真机实用暴露，非里程碑）：①`3b74a42` CR-M8-1 decompose/T1 注入自死锁——未提交写事务内同步等 daemon ack，真适配器 ack 前必发 agent.status 写 DB 撞锁撕连接（daemon-sim 不发前置帧故历届 verify 未暴露）→ 预检 `agent_daemon_online` + 注入挪 `tx.after_commit`（铁律 4 同族；**L4 已把该家族收干：读循环并发地基 + discard/reevaluate 同修**）；②`62832f4` CR-M8-2 MCP stdio win32 GBK → `run()` 双向 reconfigure UTF-8。基线更新：pytest **1089**/vitest 463。 |**CR-M8 双修**（MVP 后首次真机实用暴露，非里程碑）：①`3b74a42` CR-M8-1 decompose/T1 注入自死锁——未提交写事务内同步等 daemon ack，真适配器 ack 前必发 agent.status 写 DB 撞锁撕连接（daemon-sim 不发前置帧故历届 verify 未暴露）→ 预检 `agent_daemon_online` + 注入挪 `tx.after_commit`（铁律 4 同族）；②`62832f4` CR-M8-2 MCP stdio win32 GBK——claude 写 UTF-8 而 MCP 子进程 locale 解码，中文载荷 mojibake/崩循环/静默丢请求致 claude 无限挂起 → `run()` 双向 reconfigure UTF-8 + 解析失败必回 parse error（不许无回声）。教训：**跨进程同步等待不得跨持锁事务**、**win32 一切子进程管道显式 UTF-8**（GIT-CALIBRATION 家族第三例）、**探针自带的 env 会掩盖被测缺陷**（PYTHONIOENCODING 继承）。基线更新：pytest **1075**/vitest 403。 |
| M7 提交链 | M7a：`6eb78ff`(波1 K0+K1+K2-cal)→`9cf4318`(波2 K2+B-M7-1)→`3812f06`(波3 K3)→`9a2b17c`(verify 14/14)→`82ebd1b`(code-review)→`d238988`(jitter-survive D v1.0.5)。M7b：`8d26abe`(K4 部署域+0011)→`e06a793`(波2 K5∥K6∥B-M7-2)→`9082827`(波3 K7∥K8)→`f6655d1`(K9 verify 29/29)→**`b18adbe`(code-review 12 CONFIRMED 修 4 = MVP 完成)** |
| M6 提交链 | M6a：`d564ebf`→`62939f2`→`6f6fc93`→`bc70cd5`(verify 20/20)→`404aaa8`(review 修复)；M6b：`95d190c`(波1 J7+J11 骨架)→`3a78799`(波2 J8)→`832f2dc`(波3 J9 硬关口)→`3d3e12f`(波4 J10∥B-M6-2 后半)→`19fcfb5`(阶段4 审计+J11 定稿)→`818a483`(J12 verify)→`d303475`(code-review 收口)→`c37a91f`+`5165808`(文档)→**`d517624`(复审二轮 10 findings 全修)** |
| 契约版本 | A **v1.0.12**（M8a L0：summary_runs 表 + canvas_nodes.upstream_policy 列，35 表，迁移 0012）· B **v1.5.2**（M8b L7：`NodePatch.upstream_policy` 人类改档——设计 §5.2/裁决 #6 正文既定、契约影响表漏列，L7 依纪律 1 升版回改 + 设计 v1.0→v1.0.1；此前 v1.5.1 = M8a L0 NodeCreate.upstream_node_ids + replan 403 rule=O8；**错误码零新增仍 29**）· C **v1.0** · D **v1.0.5** · E **v1.5** · E2 **v1.0.1**（C/D/E/E2 M8b 零修订核对——O8 摘要/阻断/恢复/质量信号全走既有 message.created/task.updated/diagnostic 事件族 + inject_guard_feedback 既有帧）。**B-M8-2 零契约**（纯前端消费）。**Agent 工具仍 16**。事实源 = `D:\Project4work\Agenthub_7_8\engineering_docs\` 六契约 + `docx_agenthub\CoAgentia-PRD.md` + 交互说明 §12 + `orchestrator_docs\Orchestrator任务拆解设计.md` + `Orchestrator汇总设计.md`（O8 实现级权威 v1.0.1） |
| 建表批次 | …→ 0008 M6a → 0009 M6b → 0010 M7a（preview_sessions）→ 0011 M7b（deployments）→ **0012 M8a（summary_runs 新表[O8 汇总协调状态，task_id PK，直挂 workspace_id]+ canvas_nodes.upstream_policy 加列[第三例既有表加列，沿 0008/0009 先例]；列默认 strict 行为逐字节不变，summary_runs 无运行期消费——纯 schema 前移，L7 只做 W9 内核语义）** |
| 实机证据 | **M7 = [M7-EVIDENCE.md](../verify/M7-EVIDENCE.md)**（块 M7a 14/14 + 块 M7b **29/29 ALL PASS**）+ [M7A-VERIFY-results.json](../verify/M7A-VERIFY-results.json) + [M7B-VERIFY-results.json](../verify/M7B-VERIFY-results.json) + 浏览器 E2E [M7B-BROWSER-E2E.txt](../verify/M7B-BROWSER-E2E.txt)；脚本 = `scratchpad/m7a_verify.py`/`m7b_verify.py`+`m7a_appfactory.py`（可复跑，`--keep` 保活接浏览器）。历史：**M6 = [M6-EVIDENCE.md](../verify/M6-EVIDENCE.md)**（48/48）、M6A（20/20）、M5/M4/M3/M2 系列。 |

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
| **M7 预览、部署与打磨** | **PRD M7 出口**：Diff/**预览验收**→合并→**一键部署 URL+新账成本小结**（FR-11/FR-12+K7 性能小批+K8 预留位审查） | **已立项未开工**（2026-07-13，[M7-HANDOFF](M7-HANDOFF.md)） |

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

## 4. 块 M8b「O8 编排质量线」**已收口（L7·L8·L9 + B-M8-2）；接续 = 块 M8c**（2026-07-14）

> **块 M8b 收口（2026-07-14）**：后端三模块——**L7** W9 双档内核（`cb5d00b`）→ **L8** O8 汇总执行域（`7d4c910` + `e68abcd` F6 补测）→ **L9** 质量回路+话术（`872762a`）；前端 **B-M8-2 O8 可见面**（`a51637c`，纯前端 + 1 处 wsBridge，零后端/零契约/零迁移）。守门后端 **pytest 1117/4**、web **vitest 502**（B-M8-2 起点 472，+30）· pyright 0 · ruff 净 · gen 确定 · build 绿 · 工作树干净。**B-M8-2 四件**：① 汇总任务线程横幅（`SummaryBanner`，active 轮数 N/8+未覆盖 / blocked 原因+计数+force-start 恢复；态由 `lib/summary.deriveO8Banner` 从线程系统消息体派生，零新端点）② 画布汇总/partial 节点 badge ③ 节点检视器 `NodeInspector`（`upstream_policy` strict↔partial 改档，patch_node 通道，O9 门 server 挡 Agent）④ 摘要系统消息卡片化（`SummaryCard`）。**单源 = `lib/summary.ts`**（后端护栏消息体字面量契约的唯一前端镜像；后端话术漂移则 `summary.test.ts` 逐字节红）。**wsBridge 补**：threaded `message.created` 失效 `qk.thread`（qk.thread 原无 WS 失效——横幅/卡片靠此实时刷新，顺带收敛线程回复实时性）。**接续 = 块 M8c**（B-M8-3 外壳 + L10/L11/L12/**L13 m8_verify 收口 = O8 全链真机演示 §9b #11**）。**实施补形（L7 已如实登记）**：`NodePatch` 补 `upstream_policy` → **B v1.5.1→v1.5.2** + 设计 v1.0→v1.0.1（字段属设计既定要求非扩界；A/C/D/E/E2 零修订）。轮语义实施选择见 summary.py/hub.py 注释与 DEV-PLAN L8 行。

**M1–M7 全部收口** + **M8a 加固批收口**（`ab84406`）。M8a 五模块全绿：L0 契约+迁移 0012 / L1 原子建边 / L4 读循环并发地基+discard after_commit / L6 部署日志残窗 / B-M8-1 前端五件。守门 pytest 1089/4·vitest 463·pyright 0·ruff 净·gen 确定·build 绿。

**接续 = 块 M8b「O8 编排质量线」**（M8-HANDOFF §4：~~L7·L8·L9~~ ✅ + **B-M8-2**）：
- **L7 先行**（纪律 8 首改 graph 组，最需慎重）：W9 双档 satisfied 内核（kernel/graph.py 权威 + lib/graph.ts 镜像 + golden 扩 partial 判例，双跑逐字节；`_satisfied_nodes` 产双集合；landing 汇总节点默认 partial 一行；patch_node 人类可改档、Agent 403 rule=O9）。**迁移 0012 与 upstream_policy 列已随 M8a L0 落地**（防返工锚点 2「新增档不动旧档」：strict 默认必让全量既有 golden/测试逐字节不变）。
- **L8 汇总执行域**：collect_summary_inputs（有界摘要）/ summary_runs 三计数 CAS（轮=唤醒投递、人类发言不计轮且重置 stall/stall 指纹复用 fingerprint 内核/replan 预算 1 → 403 rule=O8/触顶阻断 blocked_at）/ 摘要系统消息幂等（指纹未变不重发，防自激振荡）/ 恢复。
- **L9 质量回路 + 话术**：落地带调整/提案 REJECTED → inject_guard_feedback 直投 + 线程留痕；role_templates builtin +2 节（汇总职责/质量信号）upsert 幂等。
- **B-M8-2 O8 可见面**：横幅（轮数 N/8·stall·阻断-恢复）/ 汇总节点 partial badge / 节点详情改档控件 / 摘要卡片化。

实现权威 = [Orchestrator汇总设计.md](../../../orchestrator_docs/Orchestrator汇总设计.md) v1.0.1（§4 摘要/§5 W9/§6 护栏/§8 质量回路）。契约面 = A v1.0.12（M8a L0）+ **B v1.5.2**（M8b L7 补 NodePatch.upstream_policy，见上 §4 注）；C/D/E/E2 零修订。M8b 后为块 M8c（外壳 B-M8-3+L10/L11 打招呼/L12 教程/L13 实机 verify）。

M8 之外的挂账（M9+ 需另立项，未开工）：

- **预留位审查登记**：[M7-RESERVATION-AUDIT.md](../M7-RESERVATION-AUDIT.md) 剩余 **R-1~R-9 / R-11 / R-12 / R-15**——多租户批（R-1~R-3）、多机批（R-4~R-6）、多副本批（R-7~R-8）、观察项（R-9 注入面中和）、契约授权/硬崩溃残窗（R-11 / R-12 / R-15）。~~R-10 / R-13 / R-14~~ 已入 M8（L6 / B-M8-1）。
- ~~O8 汇总执行期护栏 / 递归拆解 / 拆解质量回路~~ → **已随 M8 立项完成设计另文**（[Orchestrator汇总设计.md](../../../orchestrator_docs/Orchestrator汇总设计.md) v1.0；实施 = M8 块 M8b）。
- **部署适配器**（Vercel/Netlify 专用集成，FR-12.4 P2）· preview 运行期日志流（MVP 仅失败日志尾）· 独立 usage 页面（裁决 #15 不做）——需求出现再议。
- **通用观察项**（承接见下 §5）：briefing @全部 by-design / `_layout_positions` 双实现 / OAuth 冷启动复验 / 前端低危观察项等。

**协作模式回顾（M8a）**：Opus 单窗编排，风险面（L4a 读循环并发地基、契约/迁移正确性、守门）主循环亲做；独立纯前端件（B-M8-1 ①②③④）派 sonnet 子代理并行（子代理写码+测试，主循环 review 其 diff、核实其事实断言[preview-deck z-index/useThread enabled]、修其 tsc 不兼容[node:fs→?raw]、补 R-14/⑤）。L4a 三不变量（帧序/ack/emit）靠既有 daemon 全测回归 + 2 探针守门；`sync()` 屏障语义随非 ack 上报异步化调整（+ drain_reports queue.join 屏障）。

## 5. 挂账（非阻塞，勿当漏项重新发明；全量见 archive/M6-HANDOFF §8）

| 项 | 说明 | 归属 |
| --- | --- | --- |
| ~~前端死壳与断链批~~ | **已收口（2026-07-14，`d2d4a20`）**：F1–F13 全实现（含 F9，owner 拍板甲+F9），问题清单 = [M8-DEADSHELL-AUDIT.md](../M8-DEADSHELL-AUDIT.md) 逐条勾销、计划 = [DEADSHELL-FIX-PLAN.md](../DEADSHELL-FIX-PLAN.md)。纯前端零契约零迁移；web vitest 403→**444**（+41）、pyright+tsc 0 错、build 绿、后端零改动；/code-review 对抗式复审 8 确认全修（线程回复 thread_root_id 等）；真机浏览器实证 F1–F5 全过（`scratchpad/deadshell_verify.py`）。**唯一非死壳残项**：「新建频道」「创建 Agent」按钮归 M8 B-M8-3 | **完成**（本提交仅含 apps/web 代码，与 M8 立项文档改动分离） |
| ~~CR-M8-1 同族残留~~ | **已收口（M8a L4，`4cd79a8`）**：①hub 读循环同步写 DB → 收帧入队+独立 writer 消费（DB 写 offload to_thread，不阻塞 loop/不撞锁撕连接；_spawn 线程安全 + _system_pending threading.Lock；心跳写移出读循环）②held discard inject 挪 after_commit + 预检；③reevaluate 勘查已在提交后（既有钉住测试）。守门：daemon 全测回归 + 2 探针（写错不撕连接 / 阻塞写不阻塞读循环）。**残登记（§L4 内）**：deliver-ack 的 read_position 写（`_handle_ack`→`_write_read_position`）仍在读循环内——不在 L4a §3 盘点的 10 处内、单行小写，暂留观察（多机高负载前收） | **完成** |
| ~~M6b CR-9~~ | ~~`_post_landed_message` 逐节点 fetch_task/_member_name（N+1）~~ | **M7 K7 收**（拍板 #3） |
| **M6b CR-10** | proposals 部分唯一索引 sqlite_where 谓词字面量与 ProposalStatus 终态集双源 | 观察项（改终态集须同步；可加断言测试） |
| **M6b CR-11** | 0009 downgrade batch recreate agents 时 agent_skills FK 处置未测 | 择机（downgrade 罕跑） |
| **M6b 审计登记** | 跨进程双直落批（`landing_batches` 无 (kind,source_ref) 唯一约束）——单进程 `_landing_lock` 串行化安全，多进程非部署形态 | M7 多机化时收 |
| **M6b 审计登记** | 注入面 prompt 中和（线程摘要 verbatim 进 Orchestrator 注入体，可被伪 `[system` 操纵——单工作区信任模型内；direct 频道无人类闸放大） | 观察项（多用户化前收） |
| **M6b 审计登记** | revalidateDelta 客户端为服务端子集（防呆纵深）/ TS applyAdjustments 潜伏守卫（UI 不可达）/ graph.ts UTF-16 码元序（ASCII 值域无差,可复用 fingerprint.ts cmpCodepoint）/ activeDraft·activeDelta 终态悬挂（良性提示条）/ LandingToaster 不分频道 / P12 越界静默 | 前端低危观察项 |
| M5b 承接 | briefing @全部映射 agent（by-design）/ `_layout_positions` 双实现（尺度不同）/ ~~serialize N+1~~ | 已接受/观察/**serialize N+1 → M7 K7 收** |
| M4–M2 承接 | held 卡倒计时边角 / ~~hub usage.batch N+1+search 双扫~~（**M7 K7 收**）/ `task #n` refs UI 消费面 / `<TaskBoard>` 抽取 / messages_fts rowid（VACUUM）/ OAuth 冷启动复验 | 观察项/择机 |
| 模板域 | 模板不携 system 节点 / 实例化不提供 Project 跨频道重映射 | MVP 观察项 |

## 6. 启动方式

**真实开发**：终端 1 `uv run coagentia-server`（8787）；终端 2 `pnpm --filter @coagentia/web dev`（5173，代理 /api→8787）。
**同源构建**：`pnpm --filter @coagentia/web build` 后 `uv run coagentia-server`，开 `http://127.0.0.1:8787`（自动发现 apps/web/dist；异地设 `COAGENTIA_WEB_DIST`）。
**Mock**（显式开启才用）：`VITE_API_BASE=http://127.0.0.1:8642` + `VITE_MOCK_MODE=true` + mock-server。
**隔离实机 verify 范式**：`scratchpad/m6_verify.py`（M6 全场景 48 探针，`--keep` 保活可接浏览器截图）；基建 = `m6a_harness.py`+`m6a_appfactory.py`（临时库 alembic head+seed/真 daemon-sim/独立端口/taskkill 杀树）。

## 7. 守门命令（全绿才算收口）

```
uv run pytest -q                    # 当前 1117 passed / 4 skipped（M8b L7 起点 1089/4），只增不减
pnpm -F @coagentia/web test         # 当前 vitest 502（B-M8-2 起点 472），只增不减
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
- **L4a hub 读循环并发模型（M8a 新增，接手核心）**：daemon 读循环（`_reader`）只**收帧**——ack/reply/ping 就地处理（唤醒 Future/回 PONG），REPORT 入 `conn.report_queue` 交**独立 `_writer` 协程**消费；上报 DB 写经 `asyncio.to_thread(self._handle_report_write, …)` **offload 到线程池**（不阻塞事件循环、单帧写异常只记日志不撕连接）。三不变量：帧序（FIFO 串行）/ack（写成功后才 ack）/emit（gateway_tx 提交后 bus.emit，worker 线程经 call_soon_threadsafe 投队列，与 REST 线程池 emit 同构）。**新增 report 分支须**：`_handle_report_write` 内保持纯同步、需 ack 的 return frame_id；碰 loop 亲和状态（`self._spawn`/`self._system_pending`）——`_spawn` 已线程安全，`_system_pending` 一切读改写须 `with self._pending_lock`。**`sync()`（ping）不再保证非 ack 上报落库**（异步了）——测试观测其效果改用 `hub.drain_reports`（queue.join 屏障）或轮询。
- **铁律 4 扩（CR-M8-1→L4 收尾）**：跨进程同步等待（inject 等 daemon ack）不得跨持锁事务、读循环不持锁同步写 DB；REST 侧 inject 一律经 `tx.after_commit` + 前置 `agent_daemon_online` 预检（proposals/held discard 已循此）。
- **落地期系统节点抑制**：running 落地批期间不认领 idle merge/check（`_channel_landing_in_progress`）；**fail-closed 后不自动重扫**（截断前缀图上 merge 会空成功进不可 retry 终态）——只 LANDING_COMPLETED 触发补扫描。
- **O9 面清单**：canvas create_node/delete_node/create_edge/delete_edge/patch_node + templates instantiate 对 Agent 403 rule=O9；Agent 结构变更唯一通道 = `<control>` full/delta 提案；人类不受限（C5）。新增任何「落画布结构」端点须同口径。
- **gating 语义**（M3b 教训）：作用于投递层 = 唤醒触发 **和** 投递批双面。
- **幂等身份纪律**（M5b 教训）：落地批 req_hash 折入 source 身份；重放按构造序（ledger seq），勿按字典序。
- 实机验证起的 server/浏览器/daemon-sim 进程结束前必杀（taskkill /F /T）。
