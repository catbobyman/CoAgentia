# DEDAG 重构批任务书(去 DAG 编排 → 对话式委派)

| 项 | 内容 |
| --- | --- |
| 文档类型 | 重构批任务书(开工文件)——owner 2026-07-18 拍板「方案 A:去 DAG 转对话式委派」+ 五裁决按推荐 |
| 版本 / 日期 | v1.1 / 2026-07-18(**W0–W5 全波收口**,见 §9 执行记录) |
| 收口状态 | **✅ 全部完成(2026-07-18 当日)**:六守门全绿(pytest **971/4**·vitest **266**·pyright 0·ruff 净·gen 确定·build 绿)+ 实机探针 `scratchpad/dedag_verify.py` **16/16 ALL PASS**(真 uvicorn+真 daemon+真 git);已并回 main(不 push) |
| 分支 | `refactor/dedag`(worktree `coagentia-dedag`,基于 main `585ec41` = realtest 分支 ff 并入后) |
| 上游讨论 | 本批立项依据 = 2026-07-18 会话:DAG 职责八项分析 → 方案 A 拍板 → 契约三层拆解 → 五裁决按推荐 |
| 产品语义 | Orchestrator 不再把需求拆成画布 DAG;改为**在频道里直接委派**:理解需求 → `create_task` 建任务(@建议 owner)→ 盯交付 → 指挥合并(`trigger_merge`)→ 汇总报告。平台化、简洁化。 |

---

## 1. 裁决表(owner 2026-07-18 全部按推荐拍板)

| # | 裁决点 | 定案 |
| --- | --- | --- |
| 1 | merge 替代通路 | **人类 UI 按钮 + Orchestrator `trigger_merge` MCP 工具**(沿 trigger_deploy 先例);合并顺序由 Orchestrator 对话指挥;check/冲突派回挂到 merge 动作上 |
| 2 | 退役表处置 | **表冻结留存、只删代码路径**:models.py 模型类保留(0001 metadata.create_all 与 0012/0013 迁移依赖),不出破坏性迁移;drop 等 TS 迁移批清算 |
| 3 | 守门基线 | 删域必然降数 → **显式重置基线**并在 CURRENT-HANDOFF 留痕(「只增不减」规则的记录在案例外) |
| 4 | 分支收口 | 先 ff `agent/b1-delivery-gating-deadlock-fix` → main(`585ec41`,已执行),重构批新 worktree 开工 |
| 5 | 本批语言 | **现栈(Python)做**;纯 TS 迁移另立项(终态架构已定 TS,daemon 先行) |

## 2. 退役清单(删代码路径,不删表)

| 域 | 内容 | 量级 |
| --- | --- | --- |
| 画布域 | `canvas/service.py`、`routes/canvas.py`、投递 gating(hub `_filter_agent_delivery` gating 分支/blocked 推导/B-1 ②′ 解锁唤醒扫描)、画布 WS 事件 emit | server ~1k 行 |
| 提案域 | `orchestration/{proposal,draft,landing,delta,summary,quality}.py`、`routes/proposals.py`、`<control>` 解析钩子(messages)、修复循环、O8 汇总扫描(hub)、O9 403 面 | server ~4k 行 |
| 账本域 | `ledger/{service,replay}.py`(落地幂等重放;唯一消费者=landing/模板实例化) | server ~0.7k 行 |
| 内核两组 | `kernel/{graph,decomposition}.py` + web `lib/{graph,decomposition}.ts` 镜像 + `fixtures/golden` 对应判例;**fingerprint 组保留**(freshness/幂等身份在用) | 双侧 ~2k 行 |
| 系统节点域 | `system_nodes/service.py` 节点面(prepare_dispatch/认领抑制/DAG 序);**merge/check 执行机制抽出移植到任务级 merge 动作**(W2) | 改造非纯删 |
| 模板域 | `templates/service.py` instantiate(landing batch 消费者)、`routes/templates.py`、前端向导——模板实例化的就是 DAG,随图退役 | server ~0.8k 行 |
| 前端 | 画布页/React Flow、提案卡、草稿层+确认条、delta 面板、SummaryBanner/SummaryCard/NodeInspector、LandingToaster、模板向导、P12 编排组、wsBridge canvas/draft/delta/landing 失效 | web 大面 |
| 冻结表(不 drop) | canvas_nodes/canvas_edges/proposals/landing_batches(账本行)/summary_runs/模板表 | 零迁移 |

## 3. 保留清单(一行不动)

IM 基座(M1)/任务域与看板(M2)/L2 契约与 T7 门(验收信任链第二支柱)/worktree 派生(挂任务表,`writes_code`/`project_id` 为任务列)/Diff 代理/预览链(M7a)/部署链(M7b)/usage 三层/沉默提醒/freshness+HeldDraft/kernel fingerprint/B-4 缓冲修复/B-5 submit_task_contract(委派模式下更核心)。

## 4. 新建清单

| # | 内容 | 契约 |
| --- | --- | --- |
| N1 | `create_task` MCP 工具:Orchestrator 建任务(参数终形 = channel_id 必/text 必/title?/project_id?/writes_code?——**无 suggested_owner 参数,建议人用正文 @名字 承载**,E v1.7.1 对齐);锚点消息 @ 建议人即唤醒,认领仍走 claim 防重(O4 语义不变) | E 17→18 |
| N2 | `trigger_merge` MCP 工具 + `POST /tasks/{id}/merge` 任务级 merge 动作:复用 system_nodes 抽出的 merge 执行机制(daemon git merge --no-ff/冲突建任务派回/merge_commit 持久/conflicted 可再触发);**已 merged→幂等 202 status=merged,状态类拒绝 422**(B v1.6.1 对齐);人类按钮同端点 | E 18→19,B +1 端点 |
| N3 | Orchestrator 角色模板重写:拆解协议话术(J11 + M8b 9/10 节)→ 委派话术(规模判断保留「宁欠拆不过拆」思想→逐个派活;写 TaskPlan 骨架进任务;盯交付;指挥 merge 顺序;汇总报告含未覆盖) | 纯 builtin 数据 |
| N4 | 编排预算护栏 | **后置**——实测暴露失控后再加(裁决记录) |

## 5. 契约影响清单

| 契约 | 修订 | 内容 |
| --- | --- | --- |
| A 实体表 | v1.0.13 → **v1.0.14** | 画布/提案/账本/汇总/模板结构表标记**冻结(frozen)**;零迁移;任务表零变更 |
| B REST | v1.5.2 → **v1.6** | 退役:canvas 全部端点/proposals 全部端点/templates instantiate;新增:`POST /tasks/{id}/merge`;O9/O8 403 规则条目退役 |
| C WS | v1.0 → **v1.1** | 退役 canvas.*/draft.*/delta.*/landing.* 事件族;task/message/deploy 事件族不动 |
| D daemon | **零修订**(预期) | merge 走既有 worktree 指令通道,触发源从系统节点改为任务动作,帧形不变;核对后如需升版按纪律 1 执行 |
| E 工具 | v1.6 → **v1.7** | +create_task +trigger_merge(17→19);拆解/`<control>` 相关话术条目退役 |
| E2 | 零修订 | 适配器进程模型无涉 |
| PRD | 增补批注 | FR-9/O1–O10/画布章节标记「2026-07-18 方案 A 拍板废弃」,新增委派模式一节(简) |

## 6. 波次

W0 本任务书 + 契约文档修订 → W1 后端退役 → W2 新建(N1–N3) → W3 前端退役+替换 → W4 守门全绿+基线重置 → W5 实机探针(`scratchpad/dedag_verify.py`:真 uvicorn+daemon-sim 全链)+ CURRENT-HANDOFF 收口 + 并回 main(**不 push**)。

## 7. 验收标准(全部达成,证据 = docs/verify/DEDAG-VERIFY-results.json + 守门数字)

- [x] V1 as_task 交付字段全链(create_task 工具的 REST 同道):任务建立→纯任务驱动派生→真 git worktree 落盘(探针 D1/D2;工具→端点 1:1 映射由 daemon test_adapter_mcp 43 用例断言)
- [x] V2 writes_code 任务 worktree 照旧派生——且比原更宽:无画布门,建任务即派生(探针 D1+test_worktree_lifecycle 21 绿)
- [x] V3 done 后 REST merge → 真 git merge --no-ff、merge_commit 持久、频道系统消息;真冲突 abort 恢复主干+自动建任务派回;幂等 202;离线 503(探针 D3–D11)
- [x] V4 退役端点全部移除(探针 D12:POST 组 404/405、GET 组落 SPA catch-all 非 API;冻结表完好由 test_alembic_upgrade/test_schema_conformance/test_manifest_entities 单元证)
- [x] V5 守门六项全绿;新基线 = pytest **971 passed/4 skipped**(原 1192/4)·vitest **266**(47 文件,原 538)·pyright 0·ruff 净·gen 确定·build 绿——**基线显式重置**(裁决 #3):削减全部来自退役域测试删除(py 11 文件 233 用例+手术 ~15 用例;web 50 文件含 lib 三镜像),保留面测试零删;新增 test_task_merge 14 用例+as_task/改写若干
- [x] V6 gen 确定:两跑逐字节一致;生成物 -34 退役接口/+TaskMergeAccepted+AsTask 扩展(models.ts 762 行/rest.ts 1151 行变动)
- [x] V7 保留面不回归:preview 30/held-drafts 25/worktree_lifecycle 21/deploy·usage·contracts 全量 971 绿;修复一处真回归(quality 域退役误删 hub.inject_guard_feedback 波及 M4 护栏 discard 直投——「删面波及保留面」第二例,已修+家族化扫描全 hub 调用点无同族)

## 8. 风险与对策

| 风险 | 对策 |
| --- | --- |
| merge 机制从节点上下文抽出时丢语义(冲突派回/仅 failed 重试/幂等) | W2 以 system_nodes 既有测试为底稿改写为任务级测试,逐语义核对 |
| 删面波及保留面(hub 投递主路径与 gating 交织) | gating 删除=`_filter_agent_delivery` 只剩 worktree fail-closed 截断分支;B-1 相关扫描整体摘除;daemon 全测回归守门 |
| 0001 metadata.create_all 依赖 live models | 冻结表模型类**保留**,仅删 service/routes 消费(裁决 #2 实现要点) |
| 测试大面退役致守门虚绿 | 只删「被退役域自身」的测试;保留面测试一个不删;基线重置数字在 W4 逐项登记 |

## 9. 执行记录(v1.1 收口补记,2026-07-18)

**执行模式**:Fable 单窗主编排 + Workflow 六路并行(mock 镜像→gen 链/server 扫尾/daemon 双工具/委派话术/前端退役/py 测试清理)+ 收尾三子代理(merge 按钮/merge 域 14 测/worktree 生命周期 7 测改写)。

**关键实施决策(超出任务书原文的,均已写回契约文档)**:
1. **B v1.6.1 实现对齐**:已 merged → 幂等 202 `{status:"merged"}`(非 409,Agent 重试自然收敛);状态类拒绝归位 422(TASK_TRANSITION_INVALID/VALIDATION_FAILED);同项目串行 409=DEPLOY_IN_PROGRESS rule=W5;conflicted 树允许再触发=重试语义。
2. **A v1.0.14 修正**:ledger_entries **不冻结**(通用幂等账本基础设施在用,REST 幂等/活动/契约/文件持续读写)——八表改七表冻结。
3. **E v1.7.1**:create_task 无 suggested_owner_member_id 参数(建议 owner 走正文 @名字);text 必填/title 可选。
4. **cleanup 语义**:done+active 树不清理(委派模式下是待 merge 输入);closed/merged/cleaned 走 keep_days(原「done 终态即清」在自动 merge 节点时代安全,显式 merge 时代会清掉待合并输入)。
5. **force-start 语义收敛**:「越过 gating」→「人类催动」(直投唤醒 owner 开工,留痕不变);前端 ForceStartModal 话术重写并挂 ThreadPanel 任务牌头(owner=Agent 且 todo/in_progress 显示)。
6. **daemon 202→held 收窄**:call_tool 的 202 held 标记从「一切工具」收窄为 {send_message, create_task}——否则 trigger_merge 的 202 受理回执被误标 held 误导 Agent 停止行动(专项测试钉住)。
7. **mock 补齐 6 端点**:M3 contracts×3/force-start + M4 held-drafts 三键此前 mock 从未 serve(历史欠账);按「serve 清单全集不多不少」补齐,一致性测试升级为 M1..M7+PSWT+DEDAG 全清单双向对照。

**修复的真回归(删面波及保留面第二例)**:quality 域退役误删 `hub.inject_guard_feedback`,波及 M4 护栏 discard/reevaluate 直投(AttributeError 撕 daemon 连接)——恢复方法+全 hub 调用点家族化扫描(仅此一处)。第一例=worktree 派生原「非画布不派生」,已重写为纯任务驱动。

**新挂账(登记 CURRENT-HANDOFF §5)**:①merge 端点 409 判定与 pending 登记非原子(read-then-act TOCTOU 家族,单用户良性,daemon 侧 project lock_key 串行兜底);②使用教程 19 图含画布拆解流程截图,DEDAG 后需重摄;③UsageLevel.CANVAS 枚举值保留(语义=频道级聚合,usage.py 注释已澄清);④@xyflow/react 依赖已删(package.json+lockfile)。

**提交链**:见 main 合并后 git log(本任务书 v1.1 与代码同批提交)。
