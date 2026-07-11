# M6 Orchestrator 与 Project —— 任务书（M6-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-11，M5 收口（`bef88eb`→docs `2daf596`）后立项；契约修订核对与落笔已随本次立项**先行完成**（§1）；owner 四项方向裁决已拍板（§7 #1–#4） |
| 用途 | **M6 里程碑的唯一任务书入口**：把交付链（Project 绑定/worktree 分级派生/Diff 卡/系统节点/自动合并与冲突派回，FR-10/W8）与 Orchestrator 拆解链（提案/校验/修复循环/草稿确认/落地/delta，FR-9）装进系统。前置任务书 [M5-HANDOFF.md](archive/M5-HANDOFF.md) 已完成归档 |
| 上游事实源 | [engineering_docs/](../../../engineering_docs/README.md) 六契约（**A v1.0.8 / B v1.4.1 / C v1.0 零修订核对 / D v1.0.3 / E v1.4 零修订核对 / E2 v1.0.1 零修订核对**；v1.0.8/v1.4.1 为 M6 开工补遗，见 §1）· **[Orchestrator任务拆解设计.md](../../../orchestrator_docs/Orchestrator任务拆解设计.md)（拆解全生命周期的实现级权威：状态机/schema/V1–V14/修复循环/落地幂等/delta/Prompt/失败全表/验收 A1–A8）** · [03-接入架构建议.md](../../../orchestrator_docs/03-接入架构建议.md)（三层形态定论：角色模板数据 / 服务端领域模块 / 同构零依赖内核）· [CoAgentia-PRD.md](../../../docx_agenthub/CoAgentia-PRD.md) FR-9/FR-10/§4.8/§4.9/O1–O10/W1–W9 · [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) |
| 执行计划 | **已建**：[M6-DEV-PLAN.md](M6-DEV-PLAN.md)（波次编排 + 进度表 + 防返工锚点；随模块完成更新）；接手者上下文 = [CODEX-CONTEXT.md](CODEX-CONTEXT.md) + 仓库根 `AGENTS.md` |
| 出口标准 | **PRD M6 出口**三场景：① 一句话需求 → 拆解落地 → 两个并行任务在各自 worktree 交付 → 合并系统节点成功；② 制造一次冲突 → 冲突任务派回并解决；③ 制造一次校验失败的提案 → 自动修复重提通过（§9 逐条清单；拆解设计 §16 A1–A8 全覆盖） |

---

## 0. 一句话目标

M5 让流程可复制、runtime 可互审；M6 让系统**长出总控与手**：一句话需求经 Orchestrator 拆成可校验、可确认、可恢复的 DAG（判断归模型、控制归代码），落地任务在各自 worktree 里真实改代码，Diff 可见、合并自动、冲突派回、校验上画布——从"聊天里的协作"变成"能交付代码的流水线"。

## 1. 契约修订摘要（**已全部落笔**，2026-07-11 随立项完成——纪律 1 的完成态而非待办）

| 契约 | 版本 | 本次修订内容 | 状态 |
| --- | --- | --- | --- |
| A 实体表 | v1.0.6 → **v1.0.8** | v1.0.7：`tasks` 增 `project_id`/`writes_code`、`projects` 补 `computer_id`、handoff verdict、TaskDetail.worktree；v1.0.8 开工补遗：`worktrees.merge_commit` 补 B §12.8/D §7 已要求的持久落点，`ProjectPublic.channel_ids` 登记为第 7 个 Public 派生字段。**0008 仍一次建三表+tasks 两列，merge_commit 随 worktrees 初建落列；0009 仍仅 proposals** | ✅ 已落笔并同步 J0 |
| B REST | v1.3 → **v1.4.1** | v1.4：§12 全规范、模板治理、28 错误码；v1.4.1 开工补遗：§4.11 精确冻结 Project POST/PATCH、绑定 `{project_id}` 与 GET `[ProjectPublic]`+channel_ids，computer_id 必填且 server 不魔法推断 | ✅ 已落笔并同步 J0 |
| C WS | — | **零修订**（连续核对）：M6 预留事件族（draft.*/delta.*/landing.*/proposal.updated/worktree.updated）**C §7/§6.7 自 v1.0 起已全登记**；系统节点状态走 canvas.node_updated（既有注记）；冲突任务走 task.created；check/merge 留痕走 message.created + diagnostic——**M6 零新增事件类型**（J0 核对 contracts ws 目录，未登记者补齐属 M1 预留兑现非契约变更） | ✅ 核对完 |
| D daemon | v1.0.2 → **v1.0.3** | 交付进程帧"登记目录"转"落地形状"：`worktree.merge` 细化（显式参数/--no-ff/键=task_id+主干 HEAD/冲突 abort+conflicted 上报）；**新增 `check.run` 指令帧**（键=run_id，主工作区执行）；`git.diff` 的 **DiffPayload 形状落笔**（逐文件 unified patch+三级截断）；**新增 `check.finished` 上报帧**（缓冲重传）；`worktree.status` 扩 merge_commit/conflict_files；§12 #1 收回 | ✅ 已落笔 |
| E 适配器 | — | **零修订核对**：worktree 工作目录=消息注入（owner 拍板），消息内容对适配器不透明；**M6 工具组为空——连续第四个里程碑零新增 Agent 工具**（提案/delta = 普通消息 `<control>` 块，server 侧解析；修复循环=S1 直投既有帧） | ✅ 核对完 |
| E2 Codex | — | **零修订核对**：同 E——Orchestrator 可为任一 runtime（BYO，O1），codex Agent 当 Orchestrator/实现者/评审者全语义同 claude | ✅ 核对完 |

> **纪律 1 完成态（J0）**：契约文档修订与开工补遗已完成，contracts manifest/mock/conformance 已同步；业务实现从 J1 起消费，不再猜形状。

## 2. 范围与非目标（勿扩界）

**范围**（PRD §8 M6 行 + owner 拍板收编）：

- **Project 域**（FR-10.1，B §12.12）：Project CRUD（admin/repo_path 校验/computer_id 路由维度）+ 频道绑定 0..n
- **worktree 生命周期**（FR-10.2/W2，B §12.6）：writes_code 任务激活自动派生（分级：只读不派生）、终态 keep_days 清理、**工作目录消息注入**（适配器零修订）
- **Diff 卡**（FR-10.3/W3，B §12.7 + D §6 DiffPayload）：daemon git.diff 代理、逐文件折叠/统计、评审 Agent 与人类同源
- **系统节点执行**（FR-10.5/W8，B §12.9 + D check.run）：check（主工作区跑命令）/merge 两种 system_action 的执行面（M3b 已建壳：表列/创建路径/gating satisfied 判定）、自动触发、仅 failed 可 retry
- **自动合并与冲突派回**（FR-10.4/W5，B §12.8）：DAG 序 **--no-ff** 合并、冲突 abort + 自动建"解决冲突"任务派回原 owner、全留痕
- **评审结论枚举**（FR-7.5 挂账收编，B §12.10）：review_verdict 四值 + needs_human 自动 @人类 + builtin 评审话术更新
- **Orchestrator 拆解链**（FR-9 全条 + 拆解设计全篇）：角色模板（数据非代码）、触发三入口归一化、V1–V14 同构校验内核（py+ts+golden 双跑）、`<control>` 解析、修复循环（≤2 次直投）、草稿画布确认（CAS/调整/拒绝/对话修正 rev+1）、落地事务（decomp: 幂等/汇总节点/merge 节点自动追加/直落）、delta 增量（base 指纹/部分接受/NODE_ACTIVE）、O9 结构变更管控、single_task、stall 相关 O8 仅拆解侧（汇总执行期护栏见非目标）
- **模板治理**（挂账收回，B §12.11）：PATCH/DELETE + builtin 不可删改
- **fail-closed 持久性复核**（M5 挂账必做，B §12.5 #4）：replay 接真前复核 mark_fail_closed 回滚路径持久性
- **0008/0009 建表批次**（A §5）：M6a 三表+tasks 两列 / M6b proposals

**非目标**：网页预览 FR-11（M7；preview.* 帧/表已预留勿动）· 一键部署与成本核算 FR-12（M7）· 汇总节点的**执行期**协调循环护栏（O8 stall/replan/轮数上限属汇总运行时设计，M6 汇总节点=普通 agent 任务由 Orchestrator 认领，护栏靠既有沉默提醒兜底；O8 完整机制随 M7 或独立批）· 递归拆解/跨频道拆解（拆解设计非目标）· worktree 定向 check（M7+，check 在主工作区）· 行级 Diff 评论（MVP 线程+行号引用）· 多 Orchestrator 抢占语义（单一非终态提案规则兜底）。

### 2.1 执行切分（两块竖切，体例同 M2–M5；owner 拍板 #1：交付链先行）

| 块 | 定位 | 模块 | 收口意义 |
| --- | --- | --- | --- |
| **M6a Project 与交付链** | 先收 | J0 · J1 · J2 · J3 · J4 · J5 · J6 ＋ B-M6-1 | **不靠 Orchestrator 即可完整演示交付链**：模板/画布建两并行 writes_code 任务 → 各自 worktree 交付 → Diff 卡评审（结构化结论）→ merge 系统节点 --no-ff 合并 → check 节点跑校验；制造冲突 → 派回解决。daemon git 实操（全里程碑最大新风险面）最早暴露 |
| **M6b Orchestrator 拆解链** | 块 a 收口后开工 | J7 · J8 · J9 · J10 · J11 · J12 ＋ B-M6-2 | **收口即 PRD M6 出口**：拆解校验 V12/V13 消费块 a 的 Project 域；落地即接通块 a 的 worktree/合并管道；出口场景（拆解→并行交付→合并）自然整合，零回向依赖 |

- 切分依据：块 a 全是 **git 实操与执行域**心智（daemon 子进程/worktree/合并语义），块 b 全是 **提案与确认流**心智（校验器/状态机/事务落账）——两类心智不混批；且 V12（writes_code→project 已绑定）依赖 Project 域先存在。
- 迁移两批：0008 随 J1（块 a）、0009 随 J8（块 b）——proposals 表块 a 期间不建（与 M5"迁移不拆两次"不同源：两块各有其表，不存在提前空置问题）。
- 模块编号 **J 系列**（跳过 I 避视觉混淆；G/H 已被 M4/M5 占用）。

## 3. 现有资产盘点（拿来即用，勿重复建设；2026-07-11 立项实核）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| 落地事务器/账本全套 | server ledger/service.py:112 record / 159 lookup / 191 create_batch / 224 mark_done / 233 mark_fail_closed；replay.py:40 HandlerRegistry / 62 done_op_id / 68 replay_batch | **M5 已活**（tmpl 消费者先例）；decomp:/delta: 前缀 = 新 kind 走同一套；J9 落地事务 = 照 instantiate_template 体例写第二个消费者 |
| 模板实例化先例 | server templates/service.py:531 instantiate_template / routes/templates.py:128 | **落地批消费者的完整参考实现**：reserve-before 幂等/逐节点 create_node/连边/briefing/@角色/baseline bump/mark_done——J9 落地事务器与它共用心智与骨架 |
| create_node 全链 | server routes/canvas.py:112（锚点消息+L2+TaskPlan+节点；**kind=system 已支持**，system_status=idle 壳） | J9 逐节点建任务直接复用；J5 只补执行面不动创建面 |
| 系统节点壳 | models.py:564-578（kind/system_action/command/system_status 列 **0003 已建**）+ canvas/service.py:128-148 derive_blocked（**system success=satisfied 已实现**） | W8 的表/创建/gating 三面 M3b 已就位——J5 纯增执行触发与状态推进 |
| 图内核双跑范式 | contracts kernel/graph.py（detect_cycle/derive_blocked）+ kernel/fingerprint.py + web lib/graph.ts + fixtures/golden/{graph,fingerprint}.json | **纪律 8 的既有三处单源**；J7 校验内核（V1–V14+`<control>` 解析+decomposition.json）照此扩第三组 |
| S1 直投先例 | server hub.py:1011 inject_guard_feedback（guard_feedback 直投+诊断双写） | J8 修复循环错误清单直投 = source.kind=repair 同款路径 |
| 对账扫描挂接点 | hub.py:829 reconcile（#4 落地重放/#5 worktree 派生/#6 修复续传 **D §4.4 已登记**，M6 接真） | J3/J8/J9 的离线恢复面不发明新机制 |
| S2 CAS 骨架 | B §5 规范 + M3b canvas baseline_version/baseline_hash 已运转 | J9 confirm CAS 的指纹与串行化点现成 |
| 频道编排配置列 | models.py:245-246 decomp_mode/decomp_node_limit（**M1 已建**，enums.py:254 DecompMode） | J8 直落判定/V6 上限直接读列；B-M6-2 只补 P12 设置 UI |
| daemon 缓冲重传骨架 | daemon 上报缓冲（diagnostics/usage 批,JSONL 落盘） | check.finished 照 usage.batch 体例挂缓冲类 |
| daemon 查询帧骨架 | home.tree/home.file 只读代理（10s 超时→DAEMON_OFFLINE） | git.diff 查询帧照此体例 |
| 沉默提醒管道 | tasks/silence.py + hub 后台扫描 | AwaitingConfirm 24h 提醒（F5）挂接现成阈值机制 |
| held 卡/wsBridge 前端范式 | web HeldDraftCard + wsBridge held_draft.* 案例 | 提案卡/草稿确认条/worktree 徽标照此体例；M6 预留事件族接 wsBridge |
| React Flow 画布 | web CanvasTab（节点/边/blocked 徽标/ForceStartModal） | B-M6-1 系统节点形态、B-M6-2 草稿 overlay 层挂此 |
| human_members 路径 | M4b #8/#9 挂账所指的跨模块骨架 | J6 needs_human @人类 = 新消费场景，**顺路评估 DRY 收敛** |

**确认缺口**（施工面）：orchestration/ 空占位包（proposal/draft/landing 领域模块全新）· projects/channel_projects/worktrees/proposals 未迁移 · **daemon 侧 git 实操零先例**（worktree add/remove/merge/diff 子进程封装全新——win32 路径/锁/编码坑未知，J3 实测校准先行）· `<control>` 解析器/V1–V14 校验器不存在 · web：Diff 卡/Project 设置组/草稿层/提案卡/delta 面板全新。

## 4. 线 A：后端任务分解（模块 → DoD）

| # | 块 | 模块 | 内容（契约出处） | 完成判据（DoD） |
| --- | --- | --- | --- | --- |
| J0 | **M6a** | 契约登记同步（**开工第一步之一**） | contracts 包：`ENDPOINTS_M6`（B §4.10 编排 4 + §4.11 M6 行 7 + §4.9 retry + §4.12 模板 2）、`ProjectPublic/Create/Patch`、`WorktreePublic`、`DiffPayload`（D §6 形状）、`ProposalPublic`（形状先登记，0009 随 J8）、`TaskPublic` 扩 project_id/writes_code、`TaskDetail` 扩 worktree、`TaskHandoffBody` 扩 review_verdict（枚举 `ReviewVerdict`）、`CanvasNodeCreate` 扩两字段、ErrorCode 28、**ws 事件目录核对补齐 M6 预留族**（C §7 一名两用清单——M1 预留兑现非契约变更）、D 帧模型 +check.run/check.finished/worktree.merge 细化/worktree.status 扩字段；mock 补 projects/diff/proposals 形状（纪律 4） | manifest/catalog 测试红转绿；`pnpm gen` diff 为空；mock 一致性扩展全绿 |
| J1 | **M6a** | 0008 迁移 | Alembic `0008_m6a`：projects + channel_projects + worktrees 三张（A §4.9，含 computer_id 与 v1.0.8 merge_commit）+ **tasks 加 project_id/writes_code 两列**；models ORM + `M6A_TABLES`；索引最小集（worktrees.task_id UNIQUE；channel_projects 复合 PK） | 从零 `upgrade head` 与 M5 库增量升级双路绿；表结构对照测试扩展；加列后既有 tasks 全量测试零回归 |
| J2 | **M6a** | Project 域 | B §4.11/§12.12：routes/projects.py——CRUD（admin 门 R 矩阵/repo_path 存在+是 git 仓库 422 就地/computer_id 必填校验/DELETE 活动树 409 PROJECT_IN_USE）+ 频道绑定 POST/DELETE（admin）；`GET /projects` 响应携 `channel_ids` 读面派生（**裁决 #9：零新端点**，工作区级小表全量） | 端点逐路径（权限/校验/绑定/409/派生字段）；conformance 双跑扩展 |
| J3 | **M6a** | worktree 生命周期（**git 实操 A 级实测校准最先做**） | daemon：`git.py` 子进程封装（worktree add/remove/merge/diff——**先真机戳 win32 行为**：路径分隔/中文路径/锁文件/长路径，坑记录进 DEV-PLAN）+ worktree.ensure/cleanup 处理器（自然键幂等 D §5.3）+ worktree.status 上报；server：**激活联动**（画布节点激活判定点挂 ensure 下发——derive_blocked 非 blocked 且 writes_code=1 且无 worktrees 行）+ 对账 #5 接真 + 终态清理调度（hub 周期扫描 keep_days，D 调度归 server）+ **工作目录消息注入**（唤醒/激活/briefing 话术拼接 worktree 绝对路径，B §12.6 #4——适配器零修订） | ensure 幂等（重发 noop+现状上报）/激活才派生/只读不派生/清理调度到点/离线重连对账补派生（D §11 #1 半边）/注入话术进投递消息断言 |
| J4 | **M6a** | Diff 卡链路 | D §6 git.diff 查询帧（DiffPayload：逐文件 status/±/unified patch/三级截断）+ server `GET /tasks/{id}/diff` 代理（无树 404/离线 503）+ TaskDetail.worktree 读面派生（serialize 联查） | daemon 侧 diff 形状逐例（增删改/重命名/二进制/截断）；端点代理与超时；TaskDetail 字段回归 |
| J5 | **M6a** | 系统节点执行与合并链 | B §12.8/§12.9：引擎触发器（任务 done→derive→系统节点非 blocked 且 idle→自动触发：check→D check.run 下发、merge→worktree.merge **DAG 序**逐上游）+ `POST /canvas-nodes/{id}/retry`（仅 failed，409 SYSTEM_NODE_NOT_RETRYABLE）+ check.finished 处理（system_status 推进+node_updated+系统消息+diagnostic）+ merge 成败处理（merged 记 commit / **conflicted→自动建"解决冲突"任务**：L2/owner=原 owner/writes_code 同 project/锚点附冲突清单/连边冲突任务→merge 节点）+ 全留痕 | check 成/败/retry/重发终态幂等；merge --no-ff 成功推进 gating；冲突派回全链（任务/连边/锚点内容）断言；触发器幂等（重复 done 事件不重复下发） |
| J6 | **M6a** | 评审结论枚举（FR-7.5 收编，owner 拍板 #3） | B §12.10：TaskHandoffBody.review_verdict 四值（J0 已登记）；提交含 needs_human 的 handoff → 系统消息 @频道全体人类（**human_members M4b #9 挂账顺路评估收敛**）；builtin 工程三角评审节点话术更新（引导结构化填写；启动 upsert 随版迭代既有机制） | verdict 逐值合法/缺省向后兼容/needs_human @人类消息断言/builtin upsert 幂等回归 |
| J7 | **M6b** | 同构校验内核（纪律 8 扩展第三组） | B §12.2：contracts kernel 新模块——`<control>` 解析（恰一块/围栏容忍/CONTROL_PARSE）+ V1–V14 全量收集校验器（安全子集/字段别名 hint/errors v1 形状）+ 提案规范化指纹（复用 fingerprint，nodes/edges 排序）；web `lib/decomposition.ts` 镜像；`fixtures/golden/decomposition.json` 判例（红绿例：环/引用悬空/别名/上限/single_task 带边/V10 契约缺失/V12 project/V14 system 节点） | golden 双跑逐字节一致（py+vitest）；判例覆盖 V1–V14 每条至少一红一绿 |
| J8 | **M6b** | 0009 迁移 + 提案域 | Alembic `0009_m6b` proposals（A §4.8 部分唯一索引）；orchestration/proposal.py——生命周期状态机（8 态）+ 触发三入口归一化（decompose 端点：text 代发消息转任务/task_id 直取/NO_ORCHESTRATOR 409）+ **上下文注入**（成员/Project/画布摘要/频道配置拼进唤醒，拆解设计 §4）+ 提案消息解析挂接（messages 落库路径识别 `<control>`→校验→提案卡锚点）+ 修复循环（S1 直投 source.kind=repair/repair_count per revision/≤2 穷尽 Failed @人类）+ Superseded 管理（对话修正 rev+1/同指纹不加）+ 对账 #6 接真 + AwaitingConfirm 24h 沉默提醒挂接 | 状态机逐边/单一非终态 DB 兜底/三入口归一/注入内容断言/解析成败路径/修复两轮穷尽升级/rev 重置配额/离线续传 |
| J9 | **M6b** | 草稿确认与落地（**含 fail-closed 复核必做**） | orchestration/draft.py+landing：confirm CAS（S2 指纹 409 STALE_CONFIRM 携最新态）+ 调整全量重验（服务端权威）+ adjustments/landed_hash 落账 + reject（理由进线程）+ **落地事务**（decomp: 前缀/拓扑序 create_node 复用/汇总节点条件追加/**merge 系统节点自动追加**裁决 #6/直落 auto(channel-policy)/:done 后发已落地消息）+ replay 对账 #4 接真 + **fail-closed 持久性复核**（mark_fail_closed 在 ApiError 回滚路径下持久——独立连接或前置提交 + 确定性测试，M5 挂账收编） | confirm CAS 逐路径/调整重验红例/落地幂等（重放恰一批/崩溃续段）/汇总+merge 节点追加断言/直落跳确认/fail-closed 复核测试红转绿/**A5 用例**（落地中 kill→重启补齐无重复） |
| J10 | **M6b** | delta 增量与 O9 管控 | 拆解设计 §11：decomposition-delta.v1 校验（操作应用基线后结果图全规则）+ base 指纹不符 409 DELTA_BASE_MISMATCH + NODE_ACTIVE 422 + **部分接受**（confirm 内 removed_ops/delta_landed_hash/剔除清单可读）+ delta 落地（`delta:<batch_id>:op:<index>`）+ **O9 拦截**（裁决 #10：canvas 结构写端点对 Agent 主体 403 rule=O9；Agent 结构变更通道=消息 `<control>` delta 提案；人类不受限 C5） | delta 校验红绿/base 过期拒/活动节点拒删/部分接受重验+落账/幂等/Agent 403 与人类放行分野 |
| J11 | **M6b** | Orchestrator 角色模板 + 模板治理顺手件 | 角色模板 = **数据非代码**（接入架构 ①层）：builtin 角色模板记录（拆解章节 prompt §13.1+规模判断表 §12 注入位）+ 创建向导预选 + NO_ORCHESTRATOR 前端引导数据面；**PATCH/DELETE /templates**（B §12.11：仅元数据/builtin 409 TEMPLATE_BUILTIN_IMMUTABLE/历史引用不阻删） | 模板记录 upsert 幂等/创建预选链路/治理端点逐路径（builtin 拒/引用不阻删） |
| J12 | **M6b** | 端到端实机 verify（**块 b 收口 = PRD M6 出口**） | 真机（隔离库+独立端口+**脚本生成 scratch git 仓库**裁决 #15+真 daemon）：建 Orchestrator（真机 runtime）→ 一句话需求 @Orchestrator → 提案卡+草稿画布 → 人工调整（删节点/改 owner）→ 确认落地 → 两 writes_code 任务并行 worktree 交付（消息注入路径）→ Diff 评审 → merge 系统节点 --no-ff 合并成功 → check 节点绿；**场景②**制造冲突→conflicted→冲突任务派回→解决→retry 合并成功；**场景③**测试桩致校验失败→修复循环自动改好；连续 2 次失败→Failed @人类；顺带：single_task、直落频道、delta 部分接受、A5 崩溃重放 | §9a+§9b 清单收口 + 截图/证据归档 `docs/verify/M6-EVIDENCE.md`（拆解设计 §16 A1–A8 逐条勾销） |

**推进顺序**：**块 M6a**：J0 ∥ J1 并行（contracts 包 / migrations 文件域不相交）→ **J3 的 git 实测校准紧随最优先**（win32 git worktree 实操是全里程碑最大不确定性——先真机戳行为再写处理器）→ J2 ∥ J4 并行（routes/projects / daemon+diff 端点）→ J5（消费 J3 的 merge 封装）→ J6（独立小件可与 J5 并行）；**块 M6b**：J7 → J8 → J9 → J10 串行（校验内核→状态机→落地→delta 层层消费）→ J11（可与 J9/J10 并行——模板治理独立）→ J12 收口。B-M6-1/B-M6-2 契约形状就位（J0）后即可吃 mock 开工。

## 5. 线 B：前端任务分解（模块 → DoP）

> 沿用设计线 verify SOP：优先消费归档稿 `docx_agenthub/04-设计稿/afterglow-ds/previews/`（P2b 草稿层/P2c delta/P12 设置/C2 Diff 卡等有稿则对照，无稿按既有组件体例）→ playwright 1440×900 对照 → 复发点自查。

| # | 块 | 模块 | 内容 | 完成判据 |
| --- | --- | --- | --- | --- |
| B-M6-1 | **M6a** | Project 设置 + Diff 卡 + 系统节点 + verdict | ① 频道设置弹窗 **+Project 组**（绑定/解绑清单、工作区级 Project CRUD 入口弹窗：name/repo_path/命令/keep_days，admin 才见——M5 裁决 13 预留组接真）；② **Diff 卡**（交付卡/任务详情入口：逐文件折叠/±统计/unified patch 等宽渲染+行前缀色/截断提示/离线 503 与无树空态）+ 交付卡 worktree 徽标（branch/status 色，`worktree.updated` 实时）；③ **系统节点画布形态**（merge/check 图形区别于 agent 节点、system_status 四态色、failed 显 Retry 按钮、check 输出尾部查看入口）；④ review_verdict 徽标（handoff 卡四值色/needs_human 高亮横条）；⑤ 冲突任务卡（锚点消息冲突清单渲染）；wsBridge 接 worktree.updated | 行为测试（绑定流/Diff 折叠与截断/retry 按钮态/verdict 徽标/离线空态）+ 屏对照截图 |
| B-M6-2 | **M6b** | 拆解入口 + 提案卡 + 草稿层 + delta | ① 拆解入口（任务卡"拆解"动作 + 画布工具栏 + **无 Orchestrator 创建引导**：预选角色模板弹窗，交互 §6.8）；② **提案卡**（线程内：模式/节点数/依赖缩略/指纹短码/生命周期态——`proposal.updated` 驱动；失败态附错误清单入口）；③ **草稿画布层**（半透明节点+虚线边 overlay 渲染 proposals.body/顶部常驻确认条/拖拽增删改**实时过 TS 镜像内核**——确认按钮 disabled 防呆先于报错/确认携指纹 CAS/409 刷新最新态/拒绝理由弹窗）；④ **delta 面板**（绿红高亮增删项/**部分接受逐 op 剔除**/剔除后实时重验/base 过期提示重出）；⑤ 对话修正 rev 替换（draft.superseded→新 draft.presented 层替换）；⑥ P12 频道设置 **+编排组**（decomp_mode 草稿/直落 + decomp_node_limit）；wsBridge 接 draft.*/delta.*/landing.*/proposal.updated 事件族 | 行为测试（引导弹窗/确认条防呆/CAS 409/部分接受重验/rev 替换/直落配置）+ 屏对照截图 |

## 6. 纪律（沿用 M2–M5 八条，本里程碑强调三点）

1. 契约 ↔ manifest 双向同步（J0 收口中间态；D 帧形状变更随 J3/J4/J5 实测若需再修，升版回改文档——M5 H2 先例）。
2. 生成物只经脚本重生成，diff 为空守门。
3. 一致性测试套件双跑复用：M6 端点直接扩 `test_conformance_dual.py`。
4. mock 是形状源不是逻辑源：校验器/落地事务/合并语义/git 实操只活在真 server/daemon。
5. 每完成一个模块：更新 M6-DEV-PLAN §进度表 + [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)；阶段结论沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问；已拍板勿再问（§7 #1–#4 即已拍板项）。
7. 值域/判定语义只写一处：V1–V14/`<control>` 解析/指纹活在 contracts kernel 单点，前端镜像靠 golden 双跑守门，**前端不复制判定逻辑只复制内核**。
8. **纪律 8 扩展（图算法→确定性内核单源）**：graph / fingerprint / **decomposition** 三组内核均为"py 权威 + ts 镜像 + golden 判例双跑"——改动任一语义必须三处同步；**daemon 是执行器不是决策者**（D 铁律 1）：DAG 序、冲突处置、gating、触发判定全在 server，daemon 只执行 git 命令与上报。

## 7. 本任务书裁决（实现按此执行；#1–#4 = owner 拍板 2026-07-11，其余 owner 可否决，否决处升契约版本回改）

| # | 裁决 | 依据 |
| --- | --- | --- |
| 1 | **交付链先行**：M6a=FR-10/W8、M6b=FR-9；出口整合场景落 M6b 收口 | owner 2026-07-11；daemon git 实操早暴露 + V12 依赖 Project 域 |
| 2 | **worktree 工作目录 = 消息注入**：server 话术拼绝对路径，适配器 E/E2 零修订；Agent 越界写主工作区仅诊断留痕 | owner 2026-07-11；B §12.6 #4；拆解设计开放问题 2 MVP 态度 |
| 3 | **挂账三件全收**：FR-7.5→J6（M6a）、模板 PATCH/DELETE→J11（M6b）、fail-closed 复核→J9（必做——replay 接真前） | owner 2026-07-11；B §12.10/§12.11/§12.5 #4 |
| 4 | **合并 = `--no-ff` merge commit**（保任务分支历史，message 含任务编号/触发留痕）；冲突不 rebase 直接派回 | owner 2026-07-11；B §12.8/D §5.3 |
| 5 | check 系统节点在 **repo 主工作区**执行（合并后校验语义）；worktree 定向 check 归 M7+ | B §12.9 #3 |
| 6 | **merge 系统节点自动追加时机 = 落地时**（仅提案途径：含 writes_code 且未显式声明时，插全部 writes_code 叶子后、汇总节点前）；模板/手工画布不自动加 | B §12.5 #3；W8 结构显式可见优于合并期隐式插入 |
| 7 | 系统节点 retry **仅 failed**；success 终态不可重跑（下游 gating 已解锁）；idle 由引擎自动触发无手动抢跑面 | B §12.9 #2 |
| 8 | 分支名 `coagentia/task-<task_id>`（ULID 全局唯一防跨频道同 repo 撞名）；树路径 `worktrees/<project_id>/<task_id>/`（D §9.1 既定） | B §12.6 #2 |
| 9 | Project 频道绑定读面 = `GET /projects` 响应携 `channel_ids` 派生数组（工作区级小表全量，**零新端点**） | J2；B §4.11 目录不增行 |
| 10 | **O9 拦截形态**：canvas 结构写端点对 Agent 主体 403（rule=O9）；Agent 结构变更唯一通道 = 消息 `<control>` 块（full/delta 提案）；人类直接编辑不受限（C5） | B §12.4/拆解设计 §11；判断归模型控制归代码 |
| 11 | repo_path 校验 MVP server 直查文件系统（单机同机）；多机改走 daemon 查询帧（B §8 #4 已登记，勿当漏项） | B §12.12 #1 |
| 12 | Orchestrator = **角色模板数据**（daemon/适配器零专属代码）；修复循环状态机与计数在 server；提案/delta 消息与普通 Agent 消息同路（含 freshness 门——被扣走 HeldDraft F4） | 接入架构 ①层/拆解设计 §2 |
| 13 | **M6 零新增 WS 事件类型**（C 连续零修订——预留族 M1 已登记，J0 兑现登记非变更）；**零新增 Agent 工具**（连续第四个里程碑）——Orchestrator 只会"发消息" | C §7/E 核对 |
| 14 | 迁移两批：0008（M6a 三表+tasks 两列，worktrees 含 merge_commit）/ 0009（M6b proposals）——每块一批,块内一次建齐 | A v1.0.8 |
| 15 | 实机 verify 的 Project = **脚本生成 scratch git 仓库**（临时目录 init+种子提交,与本仓库隔离;launcher 脚本体例同 M3B 先例) | J12；勿拿 coagentia 仓库当靶子 |
| 16 | 汇总节点 M6 = 普通 agent 任务（owner=Orchestrator，靠沉默提醒兜底）；O8 执行期护栏（stall/replan/轮数）不进 M6（§2 非目标） | 拆解设计 §10"另文"；PRD §8 M6 行未含 |

## 8. 挂账（承接 CURRENT-HANDOFF §5；勿当漏项重新发明）

| 出处 | 问题 | 归属 |
| --- | --- | --- |
| ~~M5b~~ | ~~fail-closed 持久性（node-mismatch 分支）~~ | **J9 收**（裁决 #3 必做） |
| ~~M5 挂账~~ | ~~模板 DELETE/PATCH 端点~~ | **J11 收** |
| ~~M5 前拍板~~ | ~~FR-7.5 评审结论枚举~~ | **J6 收** |
| M5b 观察 | `_layout_positions` 与前端 `TemplateDagThumb` 分层重复（尺度/用途不同） | 观察项；J9 落地布局若三处复用再评估抽取 |
| M5b 性能 | serialize `_plan_skeleton` N+1（稀有路径） | 性能小批 |
| M5b 已接受 | briefing @全部映射 agent（含 blocked 下游 owner，by-design） | 已接受；J9 落地 briefing 同语义 |
| M5 已接受 | codex CODEX_HOME 父目录未 chmod 0700（NFR5 信任模型内） | 已接受 |
| M5 顺手 | cron 描述文案双处（daemon/mcp.py 与 server） | 顺手小批 |
| M5 观察 | ChannelsSnapshot 通知行无分页 | 观察项 |
| M4b #8/#9 | held 系统消息骨架 / human_members 跨模块 DRY | **J6 needs_human @人类是新消费场景——顺路评估收敛**（裁决随 J6） |
| M4b 观察 | held 卡升级态倒计时显示边角 | 观察项 |
| M2 挂账 | hub usage.batch N+1 / search 双扫 | 性能小批 |
| M2 观察 | `task #n` refs 无 UI 消费面 / P11·P3 看板抽 `<TaskBoard>` | 顺手评估 |
| M3/M1 观察 | messages_fts 键于 rowid（VACUUM）/ OAuth 冷启动复验 | 观察项/择机（J12 真机可顺路复验 OAuth） |
| **本任务书新增** | O8 汇总执行期护栏（stall/replan/轮数上限/有界状态摘要） | M7 或独立批（裁决 #16） |
| **本任务书新增** | worktree 定向 check / 行级 Diff 评论 / writes_code 声明失真检测 | M7+（拆解设计开放问题 2） |
| **本任务书新增** | 拆解质量回路（proposal_hash vs landed_hash 差距回流 Orchestrator 记忆） | 待汇总设计一并定（拆解设计开放问题 3） |

## 9. M6 出口验收清单（按块分组；两块全绿即里程碑收口）

### 9a. 块 M6a「Project 与交付链」清单

- [x] 1. J0 契约登记：ENDPOINTS_M6 / Project·Worktree·Diff·Proposal 模型 / TaskPublic·TaskDetail·TaskHandoffBody·CanvasNodeCreate 扩字段 / 错误码 28 / ws 预留族核对 / D 新帧 / mock 形状，catalog 与 `pnpm gen` 两跑一致
- [x] 2. Alembic `0008_m6a`：三表（worktrees 含 merge_commit）+tasks 两列，从零与增量双路绿；对照测试；既有 tasks 面零回归
- [ ] 3. J3 git 实测校准结论落 DEV-PLAN（win32 worktree 行为/坑清单）；ensure/cleanup 幂等逐例；激活才派生、只读不派生；对账 #5 离线补派生；keep_days 清理调度
- [ ] 4. 工作目录消息注入：writes_code 任务唤醒/briefing 话术含 worktree 绝对路径断言；适配器零修订核对
- [ ] 5. Project 域逐路径：admin 门/repo_path 422/computer_id/绑定解绑/PROJECT_IN_USE/channel_ids 派生
- [ ] 6. Diff 链路：DiffPayload 逐例（增删改/重命名/二进制/截断三级）/无树 404/离线 503/TaskDetail.worktree
- [ ] 7. 系统节点执行：check 成/败/输出留痕/retry 仅 failed（409 逐态）/merge --no-ff 成功推进 gating/触发器幂等
- [ ] 8. 冲突派回全链：conflicted 上报→自动建冲突任务（owner/writes_code/锚点/连边）→解决→retry 合并成功
- [ ] 9. J6 评审结论：verdict 四值/向后兼容/needs_human @人类系统消息/builtin 话术更新 upsert 幂等
- [ ] 10. B-M6-1：Project 组/Diff 卡/系统节点形态/verdict 徽标/冲突卡 + wsBridge worktree.updated，行为测试 + 截图
- [ ] 11. **M6a 真机场景**：scratch repo + 模板/画布建两并行 writes_code 任务 → 各自 worktree 交付 → Diff 卡评审 → merge 节点合并成功 → check 节点绿；制造冲突 → 派回 → 解决 → retry 成功；截图归档
- [ ] 12. 块 a 守门：后端/前端全量测试、typecheck（pyright 0）、ruff、gen 确定、双侧 build 全绿；交接文档同步（纪律 5）

### 9b. 块 M6b「Orchestrator 拆解链」清单（全绿 = **PRD M6 出口达成**）

- [ ] 13. J7 内核：golden/decomposition.json 双跑逐字节一致；V1–V14 每条红绿例；`<control>` 解析成败路径
- [ ] 14. Alembic `0009_m6b`：proposals 双路绿；单一非终态部分唯一索引兜底
- [ ] 15. 提案域逐路径：三入口归一/NO_ORCHESTRATOR/上下文注入/解析挂接/修复循环两轮穷尽 Failed @人类/rev 重置配额/对账 #6/24h 提醒
- [ ] 16. 确认与落地：CAS 409 携最新态/调整重验/adjustments·landed_hash 落账/拒绝理由进线程/落地幂等恰一批/汇总+merge 节点自动追加/直落 auto(channel-policy)
- [ ] 17. **A5 崩溃重放**：落地中 kill → 重启补齐，任务无重复无缺失、"已落地"恰一条；**fail-closed 复核**：同键异指纹停批+告警持久（回滚路径确定性测试）
- [ ] 18. delta：校验红绿/base 过期/NODE_ACTIVE/部分接受重验落账/幂等/O9 拦截（Agent 403 人类放行）
- [ ] 19. J11：Orchestrator 角色模板 upsert/创建预选/模板 PATCH·DELETE 逐路径（builtin 409）
- [ ] 20. B-M6-2：拆解入口与引导/提案卡/草稿层防呆确认/delta 部分接受面板/rev 替换/P12 编排组 + wsBridge 事件族，行为测试 + 截图
- [ ] 21. **PRD M6 出口真机实证**（三场景全）：一句话需求 → 拆解 → 草稿调整确认 → 落地 → 两任务并行 worktree 交付 → 合并系统节点成功；冲突 → 派回并解决；校验失败 → 修复循环自动重提通过（+连续失败升级/single_task/直落/delta）；拆解设计 §16 **A1–A8 逐条勾销**；全程 WS 无刷新；截图归档 `docs/verify/M6-EVIDENCE.md`
- [ ] 22. 终收口守门：全量测试绿（基线 712+175 只增不减）；M6 阶段结论写入 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本任务书移入 archive/（README 维护约定 3）

## 10. 第一步建议

块 M6a 从 **J0 ∥ J1 并行**开工（contracts 包 / migrations 文件域不相交），**J3 的 git 实操 A 级实测紧随最优先**——win32 上 `git worktree` 的路径/锁/编码行为是全里程碑最大的外部不确定性（M5 H2"先实测后写适配"先例），先在 scratch repo 真机戳 add/remove/merge/diff 四操作并记录坑清单，再写 daemon 处理器就是照契约填空；J2/J4/J5/J6 依 §4 推进顺序跟进。**块 M6b 不与块 a 交错**——等 §9a 全绿再动（V12 消费 Project 域、落地消费 worktree 管道），保持"一块 = 一个可交接的收口"。
