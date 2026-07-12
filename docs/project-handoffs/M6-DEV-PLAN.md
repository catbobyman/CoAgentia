# M6-DEV-PLAN —— 逐模块执行计划与进度表

> 任务书 = [M6-HANDOFF.md](M6-HANDOFF.md)（范围/裁决/出口清单权威）。本文只跟踪执行进度与波次编排。体例同 [M5-DEV-PLAN.md](M5-DEV-PLAN.md)。

## 0. 编排策略（多 agent 并行，块内分波 + 波间 inline 守门）

**最大外部不确定性 = win32 上 `git worktree` 的真实行为**（路径分隔/长路径/中文路径/锁文件/进程占用删除失败/CRLF）。对策沿用 M5 先例（codex 协议立项会话先校准）：**块 a 波 1 里 J3-cal 先行**——scratch repo 真机戳 `worktree add/remove`、`merge --no-ff`、冲突/abort、`diff --numstat/-p` 四组操作，坑与结论写 `scratchpad/GIT-CALIBRATION.md`（J3/J4/J5 的权威实现参考），把 J3 从「探测未知」降为「照已知行为填空」。

### 块 M6a「Project 与交付链」

| 波 | 模块（并行） | 文件域（不相交） | 依赖 |
| --- | --- | --- | --- |
| **波 1 地基** | J0 契约 ∥ J1 迁移+ORM ∥ **J3-cal git 实测校准** | packages/contracts(+ts,+mock) / apps/server(db,migrations) / scratchpad（零产品代码） | 无（三者互不依赖） |
| — inline 守门 1 | 主循环跑 pytest+gen+typecheck；修集成缝 | — | 波 1 全绿才进波 2 |
| **波 2 执行域** | J2 Project 域 ∥ J3 worktree 生命周期 | apps/server(routes/projects) / apps/daemon(git.py+处理器)+apps/server(hub 激活联动·对账 #5·注入话术) | J2/J3 吃 J0+J1；J3 吃 GIT-CALIBRATION |
| — inline 守门 2 | 全量测试+gen | — | 全绿 |
| **波 3 交付面** | J4 Diff 链路 ∥ J5 系统节点执行与合并链 ∥ J6 评审结论枚举 ∥ B-M6-1 前端 | apps/daemon(diff)+apps/server(routes/tasks diff) / apps/server(canvas 触发器+hub merge 调度) / apps/server(contracts 消费+builtin 话术) / apps/web | J4/J5 吃 J3 的 git.py；J6 独立；B-M6-1 吃 J0 mock 形状 |
| — inline 守门 3 | 全量测试+typecheck+ruff+gen+双 build | — | 全绿 |
| **实机 verify（M6a 出口）** | scratch repo + 两并行 writes_code 任务 → worktree 交付 → Diff 评审 → merge --no-ff → check 绿；冲突→派回→解决→retry | — | §9a #11 |
| **/code-review high** | 8 角度 review → 修 | — | 块 a 收口 |

- 契约集中：**J0 独占 packages/contracts 全部改动**（含 conformance 双跑扩展），后续波 agent 不碰 contracts。
- J1 独占 models.py（三表 ORM + tasks 两列 + M6A_TABLES），J2/J3 只消费。
- daemon 文件域：J3 建 `git.py` 与 worktree 处理器；J4 只**追加** diff 函数与查询帧处理，不改 J3 已写文件的既有函数（波序保证）。
- route 注册：J2 新建 routes/projects.py（需挂 routes/__init__.py——**块 a 唯一一处**，J2 独占）；J4 扩既有 tasks.py；J5 扩既有 canvas.py。

### 块 M6b「Orchestrator 拆解链」（块 a 收口后开工）

| 波 | 模块（并行） | 文件域（不相交） | 依赖 |
| --- | --- | --- | --- |
| **波 1 内核** | J7 同构校验内核 ∥ J11 角色模板+模板治理 | packages/contracts(kernel)+apps/web(lib)+fixtures(golden) / apps/server(templates 域+builtin 记录) | J7 无依赖；J11 独立域 |
| — inline 守门 | pytest+vitest（golden 双跑）+gen | — | 全绿 |
| **波 2 提案域** | J8 0009 迁移+提案生命周期 | apps/server(migrations+orchestration/proposal+messages 解析挂接+hub 直投/对账 #6) | 吃 J7 内核 |
| **波 3 确认与落地** | J9 草稿确认+落地事务+fail-closed 复核 ∥ B-M6-2 前半（拆解入口/提案卡/wsBridge 事件族） | apps/server(orchestration/draft·landing) / apps/web | J9 吃 J8；B-M6-2 吃 J0 mock |
| **波 4 增量** | J10 delta+O9 拦截 ∥ B-M6-2 后半（草稿层/delta 部分接受面板/P12 编排组） | apps/server(orchestration+canvas 403 门) / apps/web | J10 吃 J9 |
| — inline 守门 | 全量测试+typecheck+ruff+gen+双 build | — | 全绿 |
| **实机 verify（J12 = PRD M6 出口）** | 三场景全链 + single_task/直落/delta/A5 崩溃重放（拆解设计 A1–A8 逐条勾销） | — | §9b #21 |
| **/code-review high** | 8 角度 review → 修 | — | M6 里程碑收口 |

## 1. 进度表

| # | 模块 | 状态 | 提交 | 备注 |
| --- | --- | --- | --- | --- |
| J3-cal | git 实操 A 级实测校准 → `scratchpad/GIT-CALIBRATION.md`（win32 worktree/merge/diff/冲突行为与坑清单） | ✅ | `d564ebf` | 10/10 探针绿；见 §3 校准结论 |
| J0 | 契约登记（ENDPOINTS_M6/Project·Worktree·Diff·Proposal 模型/Task 系扩字段/ReviewVerdict/错误码目录/ws 预留族核对/D 新帧/mock/conformance） | ✅ | `d564ebf` + `62939f2` | 首轮 A v1.0.8/B v1.4.1；波 2 补 B v1.4.2/错误码 29 与 A v1.0.9/B v1.4.3 TemplateNode 交付字段，两项均经 owner 授权 |
| J1 | 0008 迁移（projects+channel_projects+worktrees 三表 + tasks 两列）+ models ORM + M6A_TABLES | ✅ | `d564ebf` | 含 merge_commit；从零+历史 M5 schema 切片增量双路绿 |
| J2 | Project 域（CRUD/admin 门/repo_path 校验/PROJECT_IN_USE/频道绑定/channel_ids 派生） | ✅ | `62939f2` | B v1.4.2/COMPUTER_HAS_PROJECTS 补遗已同步；Project+Computer+频道级联聚焦 42 绿 |
| J3 | worktree 生命周期（daemon git.py+ensure/cleanup 处理器+status 上报；server 激活联动+对账 #5+keep_days 清理调度+工作目录消息注入） | ✅ | `62939f2` | daemon 116/4 skipped；J2+J3 关键组合 175/4 skipped；A v1.0.9/B v1.4.3 模板入口补遗已贯通，模板/契约联跑 179 绿、独立审计无 High/Medium；波 2 全量 772/4 skipped |
| J4 | Diff 链路（daemon git.diff 查询帧 DiffPayload + GET /tasks/{id}/diff 代理 + TaskDetail.worktree 派生） | ✅ | 波 3 本提交（见 HEAD） | 增删改/重命名/二进制与三级截断、无树 404/离线 503/超时及 cleaned 分支均有回归；daemon JSONL 缓冲经同目录临时文件+fsync+原子替换加固 |
| J5 | 系统节点执行与合并链（自动触发器/check.run·check.finished/retry 仅 failed/merge DAG 序 --no-ff/冲突自动建任务派回/全留痕） | ✅ | 波 3 本提交（见 HEAD） | check/merge/retry/冲突任务全链接真；成功持久 `merge_commit`；取消/超时恢复主干，cleaned alias WS 广播与 keep_days 祖先保护经专项审计 |
| J6 | 评审结论枚举（review_verdict 四值/needs_human @人类/builtin 评审话术更新/human_members DRY 顺路评估） | ✅ | 波 3 本提交（见 HEAD） | 四值与缺省兼容、needs_human @频道人类、builtin upsert 绿；中立 `messages/service.py` 复用人类成员查询 |
| B-M6-1 | 前端：频道设置 Project 组+Project CRUD 弹窗/Diff 卡/系统节点形态+Retry/verdict 徽标/冲突卡/wsBridge worktree.updated | ✅ | 波 3 本提交（见 HEAD） | 31 文件/194 测试；1440×900 与 390×844 屏对照无溢出、console 0；三张截图仅作 UI 对照，不冒充实机 verify 证据 |
| — | **M6a 实机 verify** → [`docs/verify/M6A-EVIDENCE.md`](../verify/M6A-EVIDENCE.md)（§9a #11 场景+截图） | ✅ | `bc70cd5` | **真 uvicorn+真 websockets daemon-sim(真 git.py)+真 scratch 仓库端到端 20/20 ALL PASS**（两场景全链）+ 真仓库 5 commit（各含 `--no-ff` merge）+ 4 截图；verify-surfaced 观察 3 条（裸系统节点空成功/冲突任务承 owner/并发写伪锁）已登记 |
| — | /code-review high（块 a）→ 10 findings 统一修复 | ✅ | 收口提交（见 HEAD） | **8 角度 workflow → 10 findings（1 REFUTED）→ owner 拍板「#1 后台化 Design C/#7 保语义删死代码/其余全修」→ 10 条全落地 + 复验**：#1 daemon worktree 指令后台通道（单车道保序、ack 仍 op 后发、断连不取消仅 shutdown 取消）/#2 ensure 失败诊断归属 owner/channel+DIAGNOSTIC_APPENDED+累计 3 次一次性升级喊人/#3 reconnect 握手复验既有 active 行（revalidation_plans，conflicted 排除；周期对账不复验）/#4 冲突派回任务幂等（同节点未终态同树复用；二次真冲突建新）/#5 GitQueryError→422 透传 git prose（不再误归 503）/#6 CardKind.MERGE_CONFLICT 结构化冲突卡（契约→gen→后端 anchor→前端判定）/#8 diff 单进程全量切分（`_split_diff_sections` fail-closed 守卫）/#9 菱形 merge alias 广播按 worktree_row 去重（进展消息仍 per-node）/#10 fail-closed 注释。**新增回归 14 项**（帧序 drain 辅助/升级一次性含再扫描交互/复验三面+plans 单测/冲突幂等两态/菱形广播/422/card_kind 前后端负例/进程数恒定+切分单测+不串段）；m6a_verify 复验 20/20（4 轮 3 净 1 环境性 REST 超时，probe 既有 DB 锁重试脚手架佐证为环境噪声）+ 修 probe 收尾 suppress(BaseException) |
| J7 | 同构校验内核（kernel decomposition：V1–V14+`<control>` 解析+指纹；lib/decomposition.ts 镜像；golden/decomposition.json 双跑） | ✅ | 波 1 本提交（见 HEAD） | py 权威+ts 镜像+golden 50 判例（validate 5绿/34红 全规则覆盖+parse 7+fp 4）双跑逐字节一致；env=纯参数注入 {node_limit, member_ids, bound_project_ids}，**ref 语义=ULID id 精确匹配**（J8 上下文注入按此供给）；主循环审查追加修复两缺口：①内核严格度补齐至 ≥TaskPlanBody 消费（AC 四字段/深层未知字段全层级执法）②长度语义钉死 Unicode 码点（ts cpLen） |
| J11 | Orchestrator 角色模板（数据+创建预选+NO_ORCHESTRATOR 引导面）+ 模板 PATCH/DELETE（builtin 409） | ✅ | 波 1 骨架 `95d190c` + 波 2 接线 `3a78799` + **阶段 4 话术定稿（本提交）** | 治理端点/角色模板数据/建表 upsert/POST /agents 消费/前端引导面全链就位；**话术定稿（Fable 亲自）**：§13.1 七条保持 + **第 8 条 delta 通道增补**（O9 指引/四 op 目录/base 自愈承诺——与 delta.py DELTA_BASE_MISMATCH hint 携当前基线值互为兑现，修复循环一轮自愈）+ 内容不变量单测钉住 schema 串双处不漂移；真 LLM 试拆解校准随 J12 回写 |
| J8 | 0009 迁移 + 提案域（8 态状态机/三入口归一/上下文注入/`<control>` 解析挂接/修复循环 S1 直投/Superseded/对账 #6/24h 提醒） | ✅ | 波 2 本提交（见 HEAD） | 0009=proposals（部分唯一索引）+agent_role_templates+agents.role_template_key（A v1.0.10，反射式加列兼容双路）；角色模板启动 upsert+POST /agents 消费；`PROPOSAL_TRANSITIONS` 单点执法；persist_message 两相挂接（card_kind 插入时落列）；T1 顶级 @Orch 自动归一；修复配额=每 rev 2 轮（初提失败→1/2→2/2→第三败升级 @人类）；decompose strict 503 回滚/T1 best-effort+对账 #6 续传（从 body 重算全量清单）；24h 提醒纯推导防重发；主循环审查追修两条崩溃路径（awaiting 无效重提→对话修正失败版 rev+1 走修复循环；landing 期 control 一律忽略留痕）；**J8 边界止于 awaiting_confirm/failed/superseded+直落进 landing 态，confirm/reject/落地执行器归 J9** |
| J9 | 草稿确认与落地（confirm CAS/调整重验/adjustments 落账/落地事务 decomp: 幂等/汇总+merge 节点自动追加/直落/**fail-closed 复核必做**/对账 #4/A5） | ✅ | 波 3 本提交（见 HEAD） | **架构=202 异步增量落地**：confirm 短事务（CAS→调整应用 apply_adjustments 六 op→权威重验→落账→建批）→ hub `_landing_loop`(15s)+启动扫描+bus 触发的执行器**步进式**执行（每步=节点+其全部入边一个 gateway_tx，账本逐 op 记行保 A §4.7 目录；:done 事务=done 标记+bump 恰一次+已落地消息恰一条+landed）；直落=landing 态无批 → 扫描建批 auto(channel-policy)；merge 自动追加 deps=writes_code **前沿**（J5 合并面=祖先集，验证成立）；fail-closed 持久性修复=`LedgerFailClosed` 异常→app 层处理器回滚后独立连接 `persist_fail_closed`（+补 fail_closed activity，B §9.7 M6 启用）。**硬关口（Fable 亲自审查+重写 2 blocking）**：B1 节点 op 与入边 op 分事务→裸系统节点空成功窗口实体化（M6A-EVIDENCE 预警的复核落点）→重写为步进原子；B2 `_transition` 无条件 UPDATE+pysqlite 自动提交读→双确认双批→重写为条件 UPDATE（WHERE status=awaiting，竞败 409 STALE）；A5 亲跑通过（19/19 含 2 条硬关口回归） |
| J10 | delta 增量（base 指纹/结果图重验/NODE_ACTIVE/部分接受 removed_ops/幂等）+ O9 拦截（Agent 结构写 403 rule=O9） | ✅ | 波 4 本提交（见 HEAD） | **入口=classify_submission 扩展**（Agent+任务线程+delta 版本→kind=delta 提案；修复循环/对话修正 rev+1/对账 #6 均按 kind 路由校验器）；**校验=orchestration/delta.py**（自身 schema/base=canvas.baseline_hash/结构应用含 NODE_ACTIVE·级联/结果图无环+上限/新增节点内形=信封+过滤复用 kernel、path 重映射 $.operations[j].node）；**confirm 复用同两端点**（adjustments 须空/removed_ops 越界·重复·全剔除 422/base 过期=F9：409 DELTA_BASE_MISMATCH+提案 failed+线程消息 JSONResponse 提交/剩余集重验 NODE_ACTIVE 422/delta_landed_hash 无剔除==proposal_hash/条件 UPDATE 防双确认）；**落地=共享步进 runner**（remove_edge→remove_node[执行期 NODE_ACTIVE 复核 fail-closed/目标消失幂等]→add_node+入边步原子→add_edge，op_id 原始下标 OPID_DELTA_OP，done=delta:{id}:done，baseline bump 恰一次）；**O9 四端点 Agent 403**（delete_node/create_edge/delete_edge 原先无身份判定一并补齐）；+21 测试 |
| B-M6-2 | 前端：拆解入口+引导/提案卡/草稿层防呆确认/delta 面板部分接受/rev 替换/P12 编排组/wsBridge draft.*·delta.*·landing.*·proposal.updated | ✅ | 波 3（前半）+ 波 4（后半）本提交（见 HEAD） | 前半（波 3）：拆解入口 T2/T3+引导链/CreateAgentModal 角色模板段/提案卡/wsBridge proposal.updated·draft.presented·draft.superseded。**后半（波 4）**：**草稿层** DraftLayer（半透明虚线 overlay 分层布局+确认条 rev/节点/边/错误数+调整四 op 客户端累积经 lib/draftAdjust=apply_adjustments TS 镜像+validateProposal 实时防呆+confirm expected 三字段 CAS/409 latest 刷新/422 服务端清单/拒绝理由弹窗）；**delta 面板** DeltaPanel（ops 绿红高亮+逐 op 复选剔除→removed_ops 原始下标+lib/deltaOps 剩余集结构重验[悬空/环/上限/NODE_ACTIVE]+base 横幅+DELTA_BASE_MISMATCH 处置）；**rev 替换**（wsSideEffects 切激活草稿+toast）；**P12 编排组**（decomp_mode/decomp_node_limit/orch_escalation→PATCH 差异提交）；**wsBridge delta.*·landing.***（缓存 patch 纯化,toast 经 store 信号交 LandingToaster）；+52 vitest→348；截图归 J12 实机对照 |
| J12 | **实机 verify = PRD M6 出口** → `docs/verify/M6-EVIDENCE.md`（三场景+A1–A8+截图） | ⬜ | — | 块 b 出口 |
| — | /code-review high（块 b）→ M6 里程碑收口，任务书移 archive/ | ⬜ | — | 终收口 |

## 2. 守门命令（波间与收口；全绿才算过门）

```
uv run pytest -q                    # 当前 943 passed / 4 skipped（M6 起点 712/4），只增不减
pnpm -F @coagentia/web test         # 当前 vitest 349（M6 起点 175），只增不减
pnpm typecheck                      # pyright 0 + 双 tsc
uv run ruff check .
pnpm gen                            # 后 git diff 应为空（生成物确定性）
pnpm -F @coagentia/web build
```

## 3. 关键实现锚点（防返工；行号 = 立项实核 2026-07-11）

- **落地事务器/账本**：`ledger/service.py`（record:112 / lookup:159 / create_batch:191 / mark_done:224 / mark_fail_closed:233）+ `ledger/replay.py`（HandlerRegistry:40 / done_op_id:62 / replay_batch:68）。**J9 = 照 `templates/service.py:531 instantiate_template` 体例写第二个落地批消费者**（reserve-before 幂等/req_hash 折 source id/校验全前置于 reserve——M5b code-review 教训）。
- **系统节点壳三面已就位**（M3b）：models.py:564-578 列 / routes/canvas.py:112 create_node（kind=system → system_status=idle）/ canvas/service.py:128-148 derive_blocked（system success=satisfied）——J5 纯增执行触发与状态推进，勿动创建面。
- **S1 直投先例**：hub.py:1011 `inject_guard_feedback`——J8 修复循环 = source.kind=repair 同款路径（不进频道流+诊断双写）。
- **对账挂接点**：hub.py:829 `reconcile`——#5 worktree 补派生（J3）/#6 修复续传（J8）/#4 落地重放（J9）三条 D §4.4 已登记，接真即可。
- **内核双跑范式**：kernel/graph.py+fingerprint.py ↔ web lib/graph.ts ↔ fixtures/golden/*.json——J7 照此加第三组 decomposition；**指纹复用 fingerprint.py 勿重写**。
- **频道编排配置列 M1 已建**：models.py:245-246 decomp_mode/decomp_node_limit（enums.py:254 DecompMode）——J8 直落判定/V6 上限直接读列。
- **daemon 骨架复用**：查询帧照 home.tree/home.file 体例（git.diff）；缓冲重传照 usage.batch 体例（check.finished）；win32 子进程坑 = M5 先例（taskkill /F /T 杀进程树、stdout 必须 utf-8 decode——git 输出同样适用）。
- **幂等身份纪律**（M5b #B 教训）：落地批 req_hash 必须折入 source 身份（decomp=proposal id+landed_hash / delta=delta_landed_hash），防跨提案同键错批重放。
- **重放保序**（M5b #C 教训）：批内节点顺序以 ledger `seq` 为准，勿按 (created_at, op_id) 字典序。
- **win32 git 已校准**（J3-cal，2026-07-11）：权威记录 = [`scratchpad/GIT-CALIBRATION.md`](../../scratchpad/GIT-CALIBRATION.md)，复现脚本 = `scratchpad/run-git-calibration.ps1`，最终 10/10 探针绿。关键边界：① 正/反斜杠与中文路径均可，但 `worktree list --porcelain` 统一正斜杠；② 本机 worktree 根 193 字符成功、214 字符起即使 `core.longpaths=true` 仍报 `'$GIT_DIR' too big`，契约 ULID 路径必须保持短组件且不硬编码本机阈值；③ 显式 lock/index.lock 分别令 remove/add 退出 128，不盲目删锁；④ 文件句柄占用时 remove 退出 255，**Git 登记已消失但物理目录残留**，cleanup 必须登记+目录+prune 三面幂等；⑤ 冲突文件必须在 `merge --abort` 前用 `--diff-filter=U` 采集；⑥ Diff 元数据使用 `--name-status -z`/`--numstat -z`，二进制 `-/-` 映射 0/0+空 patch；⑦ git stdout/stderr 显式 UTF-8，默认 GB2312 会乱码；PowerShell 5.1 的 UTF-8 脚本需 BOM。

## 4. 波次守门记录

| 波次 | 后端 | web | 其余守门 | 结论 |
| --- | --- | --- | --- | --- |
| 波 1 | 724 passed / 4 skipped | 175 passed | typecheck/ruff/gen/build 绿 | `d564ebf` |
| 波 2 | 772 passed / 4 skipped | 175 passed | typecheck/ruff/gen 双跑/build 绿；模板补遗独立审计无 High/Medium | `62939f2` |
| 波 3 | 813 passed / 4 skipped | 194 passed | typecheck/ruff/gen 确定/build 绿；两项 Medium 修复后独立复核无 High/Medium；UI 双视口无溢出、console 0 | ✅ 波 3 本提交（见 HEAD） |
| **块 b 波 1** | 864 passed / 4 skipped | 261 passed | pyright 0 + 双 tsc / ruff 全绿（顺修 M6a 收口遗留 scratchpad 4 条 lint 恢复基线）/ gen diff 空 / build 绿；主循环逐文件过目 + 追加修复 J7 两缺口后收口 | ✅ `95d190c` |
| **块 b 波 2** | 895 passed / 4 skipped | 261 passed | pyright 0 + 双 tsc / ruff 全绿 / gen 确定（role_template_key 生成物属预期）/ build 绿；主循环逐文件过目 + 追修 classify_submission 两条崩溃路径后收口 | ✅ `3a78799` |
| **块 b 波 3** | 915 passed / 4 skipped | 296 passed | pyright 0 + 双 tsc / ruff 全绿 / gen 双跑确定（B-M6-2 常量迁移生成物属预期）/ build 绿；**J9 硬关口**：Fable 逐不变量对抗审查（fail-closed 时序/req_hash 折 source/构造序重放/执行器防重入/前沿 merge×J5 祖先集全过）+ 亲自重写 2 blocking（步进原子/条件转移）+ 亲跑 A5 | ✅ `832f2dc` |
| **块 b 波 4** | 936 passed / 4 skipped（+21） | 348 passed（+52） | pyright 0 + 双 tsc / ruff 全绿 / gen 双跑确定 / build 绿；COLLAB v2 阶段 3：J10 ∥ B-M6-2 后半双 Opus 子代理并行（文件域 server/web 不相交），Fable 逐文件过目集成（delta.py 校验器/步进计划/CAS 分支/O9 门/前端五件套）+ 对齐 deltaOps 头注至冻结形状后收口；两条非阻断观察归并行审计（add_edge 现端点 confirm→执行窗口人删的 FK 兜底语义/ctx.node_id AssertionError 重试而非 fail-closed） | ✅ `3d3e12f` |
| **阶段 4 并行审计+J11 定稿** | 943 passed / 4 skipped（+7） | 349 passed（+1） | **5 维 Opus finder（幂等/状态机/内核镜像/权限门/前端）审 M6b 全量 diff → Fable 逐条终裁：修 7 项**——①blocking：落地期系统节点认领抑制（`_channel_landing_in_progress`——封 delta remove 先序重开的「裸空成功」窗口）+ hub landing.completed/fail_closed 补扫描；②SM-F1：`_transition` 全面条件 UPDATE 化（StaleTransition 单源迁 proposal，draft re-export）+ classify apply 竞败降级 duplicate_ignored + supersede 遇 landing 跳过 + initiate 复用现行提案（inject=None，decompose 202 复用语义）；③SM-F2：同 source 并发建案 SAVEPOINT 降级；④SM-F3：对账 #6 channel None 跳过；⑤门 F1/F5：patch_node（可改 check command）收人类门 rule=O9 + layout 补身份；⑥门 F2：delta 已落地消息镜像 §9.3 激活节点 @suggested_owner；⑦镜像 F1：DeltaPanel activeNodeIds 补 running 系统节点 + 前端草稿/delta 面板互斥；幂等 F3/F4 顺修（插边端点先检+诊断/_PlanDefect fail-closed 防僵死）。**登记不修**：跨进程双直落批（单进程 _landing_lock 串行化,无多进程形态）/注入面 prompt 中和（信任模型内）/revalidateDelta 子集纵深/TS applyAdjustments 潜伏守卫/graph.ts 码元序（ASCII 域）/golden 补例/activeDraft 终态悬挂等前端低危 4 项——归 code-review high 复核池 | ✅ 阶段 4 本提交（见 HEAD） |
