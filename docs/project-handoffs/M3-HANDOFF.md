# M3 契约与画布 —— 任务书（M3-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-10，M2 收口（`6c12b90`）+ M2 二轮 review 修复批之后 |
| 用途 | **M3 里程碑的唯一任务书入口**：把 L2 契约与编排画布装进系统。前置任务书 [M2-HANDOFF.md](archive/M2-HANDOFF.md) 已完成归档 |
| 上游事实源 | [engineering_docs/](../../../engineering_docs/README.md) 五契约（A v1.0.3 / B v1.1 / C / D / E v1.1；**本里程碑需先行修订 E → M3 工具组**，见 §1）· [CoAgentia-PRD.md](../../../docx_agenthub/CoAgentia-PRD.md) §4.3 契约分级 D1 / T7 / §4.6 画布 C1–C8 / FR-5.3·5.4 / FR-6 / §8 里程碑 · [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)（M2 收口态与修复批） |
| 执行计划 | 待建：开工首会话按本任务书建 `M3-DEV-PLAN.md`（体例同 [M2-DEV-PLAN.md](M2-DEV-PLAN.md)：步骤分解、会话切分、测试策略、验收映射） |
| 出口标准 | **PRD M3 出口：工程三角流程在画布上跑通，blocked gating 生效**（§9 逐条验收清单） |

---

## 0. 一句话目标

M2 证明了"任务能闭环"；M3 把交付装进**契约与流程**：L2 任务有 TaskPlan/TaskHandoff schema 契约、in_review 有 T7 字段校验门、频道有画布页签（节点=任务、边=依赖）、上游未 Done 的节点 blocked 不被唤醒（人类可 force-start 留痕）——L1 轻任务拖入画布即升格补契约，流程从"活在简报文本里"变成"系统看得见、拦得住"。

## 1. 契约修订摘要（开工前先行完成的文档变更）

| 契约 | 现版 | M3 需要的修订 |
| --- | --- | --- |
| A 实体表 | v1.0.3 | **无形状变更**：task_contracts / canvas_nodes / canvas_edges 三表 v1.0 已冻结（§4.3/§4.5），`tasks.level`（l1/l2，升格只 l1→l2）M2 建表已就位；messages_fts 若换 trigram 属虚表重建，升版本号记录即可 |
| B REST | v1.1 | **无新增条文必需**：§4.7 契约端点（contracts CRUD / request-draft / force-start）与 §4.9 画布端点已预登记；错误码 HANDOFF_INCOMPLETE / GRAPH_CYCLE 已在 22 码目录。开工时若发现语义缺口（如契约确认流），按 §9 体例补规范条文并升 v1.2 |
| C WS | — | 无变更：`task_contract.created/updated`、`canvas.*`（node/edge/layout/baseline_advanced）M1 已登记（§6.4/6.5），M3 只是开始发射 |
| D daemon | — | **需核对一处**：依赖 gating 作用于投递层（PRD C3"不唤醒"）——投递判定在 server 侧（DaemonHub/唤醒引擎）挂 blocked 门，D 契约触发器不变；若需明示 gating 判定点，升版记录 |
| E 适配器 | v1.1 | **需修订 → v1.2**：§3 增 M3 工具组（契约读写面，如 `submit_contract` / `get_task` 契约摘要已有）；force-start 明确**不设工具**（C3 仅人类，v1.1 已记）；画布结构编辑是否开放 Agent 工具位（C5 全员可编辑 vs O9 结构变更管控）→ 开工首会话拍板 |

> **纪律 1 中间态**：契约文档修订先行，contracts 包 manifest 同步 = E0 模块（开工第一步），任何业务实现之前完成。

## 2. 范围与非目标（勿扩界）

**范围**（PRD §8 M3 行 + M2 移交件）：

- **L2 契约**：task_contracts 落库（TaskPlan / TaskHandoff 两 schema；LoopContract 表形状就位但生成归 M4 Reminder 线）+ 契约提交/修订端点（新 revision、旧行 superseded）+ "让 @Agent 起草"（request-draft → S1 定向直投）
- **T7 流转校验**：level=l2 的任务置 in_review 时校验 TaskHandoff deliverables/evidence 非空 → 422 HANDOFF_INCOMPLETE `{missing}`（人与 Agent 同样被拒）
- **升格机制**：L1 拖入画布 → 提示补契约（Agent 可起草、人确认）→ level l1→l2（单向）
- **画布页签 P2**：可视化（节点=任务实时着色、边=依赖）→ 拖拽编辑（建节点=**第三创建途径**、连/断边、重指派）→ 依赖 gating（blocked 标注 + 投递层不唤醒 + force-start override 留痕）
- **节点深链**：画布节点 ↔ `?task=` 双向跳转
- **M3 建表批次**（A §5）：`task_contracts` · `canvas_nodes` · `canvas_edges`
- **M2 移交浮动件**：messages_fts 换 **trigram**（中文消息 FTS 命中，A §10.4 结论）；消息级附件卡数据源（挂账 §8，M3 前处理）
- B3 遗留：React Flow 画布静态骨架并入 B-M3-2 第一步

**非目标**：freshness/HeldDraft/沉默提醒消费（M4）· LoopContract 生成与循环 Reminder（M4）· 模板存取与实例化（M5，C7 画布存为 Template 不做）· 系统节点 W8 与合并/校验（M6）· Orchestrator 拆解提案与草稿确认 S2/CAS（M6——canvases 表的 baseline 列已就位，M3 结构写只做 bump 与广播，不做 proposal 流）· 每频道通知设置（M5）。

### 2.1 执行切分（两块竖切，体例同 M2）

| 块 | 定位 | 模块 | 收口意义 |
| --- | --- | --- | --- |
| **M3a 契约与校验** | 先收 | E0 · E1 · E2 · E3 ＋ B-M3-1 | **L2 契约链路独立可验证**：提交/修订/起草请求 + T7 拒/放行 + P5 契约卡真数据；中途变故也有完整可演示成果 |
| **M3b 画布与 gating** | 块 a 收口后开工 | E4 · E5 · E6 ＋ B-M3-2 · B-M3-3 | **收口即 PRD M3 出口**（工程三角上画布 + blocked gating 生效）；只消费块 a 产物（契约端点、T7、level 语义），零回向依赖 |

- 切分依据：块 a 全是 schema 校验与修订链语义（校验器/revision/直投），块 b 全是图结构与投递门（无环/推导/gating/React Flow）——两类心智不混批。
- **FTS trigram 是浮动件**：只依赖 0002 的 messages_fts（虚表重建迁移 + 双跑对照），不依赖本里程碑任何模块；哪块顺手哪块收（同 M2 C6 先例）。
- 附件卡消息级数据源（挂账）建议挂块 a 尾部：属消息域小重构，与画布无关。

## 3. 现有资产盘点（拿来即用，勿重复建设）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| 三表形状 | `packages/contracts` entities.py | TaskContractRow/CanvasNodeRow/CanvasEdgeRow + Public **已冻结落地**（manifest 测试钉死）；`tasks.level` 列 M2 迁移已建（默认 l1） |
| 契约 schema 模型 | `packages/contracts` | **实况修正（2026-07-10 核对）**：M1 只落了 `ContractKind` 枚举（enums.py：task_plan/task_handoff/loop_contract），TaskPlan/TaskHandoff/LoopContract 的 Pydantic body 模型**全仓不存在**——E0 需按 PRD §4.3 v1 schema 补建，之后契约 body 校验才可 model_validate |
| 错误码 | rest.py `ErrorCode` | HANDOFF_INCOMPLETE / GRAPH_CYCLE / STALE_CONFIRM / NODE_ACTIVE **全部已登记**（22 码目录），E0 无需增码 |
| 画布端点契约 | B §4.9 | 快照/nodes/edges/layout/retry 全表已写死（串行化点 + baseline 附带语义）；M3 不做 retry（系统节点 M6） |
| WS 事件 | `packages/contracts` ws.py | task_contract.* / canvas.* 已登记，Envelope 复用；前端 wsBridge 未知 type 忽略原则保证增量接入 |
| canvases 表 | M1 迁移已建 | 每频道画布行 + baseline_version/baseline_hash 列就位；seed 有 4 行画布数据 |
| 基线快照语义 | A §6 | 规范快照序列化（不含 pos_x/pos_y）+ 指纹算法已定——结构写 bump 直接照抄 |
| P5 契约卡 | `apps/web` ThreadPanel | 折叠卡结构已就位（"暂无契约(M3 接入)"占位 + contracts.length>0 分支已写好 TaskPlan/AC 渲染）——B-M3-1 主要是接真不是新建 |
| 画布页签位 | `apps/web` Tabs | 画布 tab + 徽标已渲染（点击进占位屏）；React Flow 依赖未装（B3 遗留） |
| 一致性测试双跑 | `apps/server/tests/test_conformance_dual.py` | M2 端点已扩，M3 端点照样扩进去（纪律 3） |
| daemon 唤醒引擎 | `apps/server` computers/DaemonHub | 投递触发器/回执/对账 M1 已就位——gating 是在投递判定处加一道 blocked 门，不是新引擎 |

## 4. 线 A：后端任务分解（模块 → DoD）

| # | 块 | 模块 | 内容（契约出处） | 完成判据（DoD） |
| --- | --- | --- | --- | --- |
| E0 | **M3a** | 契约登记同步（**开工第一步**） | contracts 包：`ENDPOINTS_M3` 元组（B §4.7 契约组 + force-start + §4.9 画布组）、MCP 工具目录 M3 组常量（E v1.2 修订先行）、契约请求/响应模型（ContractCreate/ContractDraftRequest 等）补齐；mock 补 M3 读端点形状（纪律 4） | manifest/catalog 测试红转绿；`pnpm gen` diff 为空；mock 一致性扩展全绿 |
| E1 | **M3a** | M3 建表迁移 | Alembic `0003_m3`：task_contracts / canvas_nodes / canvas_edges **三表一次建齐**（canvas 两表块 a 期间空置，迁移不拆两次——M2 C1 先例）；task_contracts 归属/修订链约束（UNIQUE 活动 revision 语义按 A §4.3） | 从零 `upgrade head` 与 M2 库增量升级双路绿；表结构对照测试扩展 |
| E2 | **M3a** | 契约域端点 | B §4.7：`GET/POST /tasks/{id}/contracts`（提交与修订：新 revision、旧行 superseded_at；body 按 kind model_validate）+ `POST /tasks/{id}/contracts/request-draft`（202 → S1 定向直投唤醒 Agent 起草）；WS task_contract.created/updated 发射；**升格语义**：PATCH level l1→l2 单向（载体开工首会话定：独立端点或 PATCH /tasks/{id} 扩展） | 一致性双跑扩契约端点；修订链测试（新 revision 置换旧行、superseded 不可再改）；kind≠schema 拒绝路径；request-draft 直投帧断言（注入桩） |
| E3 | **M3a** | T7 流转校验 | B §4.7 status 端点追加：level=l2 → in_review 时校验**活动 TaskHandoff** deliverables/evidence 非空 → 422 HANDOFF_INCOMPLETE `{missing: [...]}`（人与 Agent 同拒，交互 §5.4）；l1 任务不受影响（M2 行为回归保障） | 逐路径测试：l2 无契约拒 / 缺 deliverables 拒（missing 指名）/ 齐备放行 / l1 直通 / Agent 经 MCP 同拒；M2 全量回归不红 |
| E4 | **M3b** | 画布结构端点 | B §4.9：快照 / nodes CRUD（agent 节点同步建 L2 任务 + 系统代发锚点消息 = **第三创建途径**；DELETE 解除引用不删任务 C8）/ edges CRUD（**无环**：写事务内拓扑排序，成环 GRAPH_CYCLE）/ layout PUT（不 bump 基线）；每次结构写在**每画布串行化点**内执行 + baseline bump + `canvas.baseline_advanced` 广播（A §6 规范快照） | 一致性双跑扩画布组；无环逐例（自环/间接环/合法 DAG）；删节点保任务；baseline 快照指纹确定性测试；WS 事件序断言 |
| E5 | **M3b** | blocked 推导 + gating + force-start | blocked = 画布边推导态**不落库**（A §4.3 注明）：上游节点任务非 done → blocked；**gating 作用于投递层**（C3/C4）：唤醒引擎投递判定处对 blocked 任务不唤醒 owner、不投递触发，**不限制状态写**（R4/R7）；`POST /tasks/{id}/force-start`：**仅人类**（Agent 403 rule=C3）、写 task_events(kind=force_start) + 任务线程系统消息、解除该节点本次 gating | blocked 推导逐例（链式/菱形/上游 done 解锁）；gating 投递测试（blocked 不投、done 后投、force-start 后投）；Agent 调 force-start 403；留痕断言 |
| E6 | **M3b** | 工程三角端到端实机 verify（**块 b 收口 = PRD M3 出口**） | 真机脚本：画布建工程三角六节点 DAG（框定→评审门→实现契约→TDD 实现→独立验收→人类终审）→ 上游未完时下游 blocked（看板+画布标注、不唤醒）→ 逐节点推进（T7 门实测：无 handoff 拒 in_review）→ force-start 一次留痕 → 全程 WS 无刷新 | §9a+§9b 清单收口 + 截图/录屏归档 `docs/verify/` |
| （浮动） | — | FTS trigram | messages_fts 虚表重建迁移（tokenize=trigram）+ 重建回填；中文子串命中实测写回 A §10.4 收口 | 中文/英文/混合检索对照测试；`GET /search` 形状不变（B §8.2 注明） |

**推进顺序**：**块 M3a**：E0→E1 串行（地基）→ E2→E3 串行（T7 依赖契约存取）；**块 M3b**：E4→E5 串行（gating 依赖边表）→ E6 收口；浮动件见缝插针。

## 5. 线 B：前端任务分解（模块 → DoP）

> 沿用设计线 verify SOP：消费归档稿 `docx_agenthub/04-设计稿/afterglow-ds/previews/`（P2 画布四稿 P2a-d）→ token 零发明 → playwright 1440×900 对照 → 复发点自查。

| # | 块 | 模块 | 内容 | 完成判据 |
| --- | --- | --- | --- | --- |
| B-M3-1 | **M3a** | P5 契约卡接真 | ThreadPanel 契约折叠卡消费真 `detail.contracts`（TaskPlan AC 列表/verify_by、TaskHandoff deliverables/evidence、revision 徽标）；"让 @Agent 起草"入口（request-draft）；T7 拒绝的 HANDOFF_INCOMPLETE `{missing}` → 缺失字段就地提示（交互 §5.4） | 行为测试 + 屏对照；l1 任务契约卡保持"暂无契约"但文案去掉"M3 接入" |
| B-M3-2 | **M3b** | P2 画布页签（B3 骨架并入） | React Flow 接入：节点=任务卡（编号/标题/owner/状态色/blocked 标注/活动细分文案）、边=依赖、实时着色（task.updated + canvas.* 双驱动）；拖拽编辑：建节点（=建 L2 任务弹层）、连/断边（成环前端 TS 预判红色反馈 + 服务端权威复核）、拖拽重指派（走 tasks/assign）；布局拖动防抖 PUT layout；节点深链 ?task= 双向 | playwright 对照 P2a-d；无环预判行为测试；WS 实时着色实证 |
| B-M3-3 | **M3b** | 升格与 force-start UI | L1 拖入画布 → 升格补契约弹层（Agent 起草 / 手填两路）；blocked 节点 force-start 按钮（二次确认 + 留痕提示，交互 §5.3；仅人类可见）；看板 P3/P11 blocked 徽标 | 升格流实测（l1→l2 + 契约生效）；force-start 全流程截图 |

## 6. 纪律（沿用 M2 七条，唯一新增第 8）

1. 契约 ↔ manifest 双向同步（E0 收口中间态）。
2. 生成物只经脚本重生成，diff 为空守门。
3. 一致性测试套件双跑复用：M3 端点直接扩进 `test_conformance_dual.py`。
4. mock 是形状源不是逻辑源：无环校验/T7/gating/基线 bump 只活在真 server。
5. 每完成一个模块：更新 M3-DEV-PLAN §进度表 + [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)；阶段结论沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问；已拍板勿再问。
7. 状态机/值域语义只写一处：TASK_TRANSITIONS/UNCLAIMABLE_STATUSES 先例延伸——blocked 推导、T7 必填字段清单、契约 kind 目录都走 contracts 常量，server 与前端共同消费。
8. **图结构语义只写一处**：无环校验与 blocked 推导的参考实现在 server（权威），前端 TS 侧只做预判反馈（红色连线），**两侧算法输入输出用同一组黄金用例对照**（防画布双实现漂移——P11/P3 看板双实现的教训）。

## 7. 本任务书裁决（实现按此执行；owner 可否决，否决处升契约版本回改）

| # | 裁决 | 依据 |
| --- | --- | --- |
| 1 | 三表一次迁移建齐，canvas 两表块 a 空置 | M2 C1 先例："迁移不拆两次" |
| 2 | blocked 是推导态不落库，由画布边 + 上游任务状态实时推导 | A §4.3 已注明；落库即第二份事实源 |
| 3 | force-start 不改任务状态、不删边：效果 = 对该节点解除本次投递 gating + 留痕（task_events + 系统消息） | C3"人类可强制启动 override（留痕）"的最小直译；状态写本就不受限（C4） |
| 4 | T7 只对 level=l2 生效；升格单向 l1→l2；M2 存量任务全部 l1 不受影响 | A tasks.level 定义；向后兼容出口 |
| 5 | T7 校验对象 = 该任务**活动**（未 superseded）的最新 TaskHandoff | 修订链语义的自然推论；无 handoff 视同缺 deliverables+evidence |
| 6 | 契约提交即生效（POST contracts 落库），"人类确认"体现为：Agent 起草产物贴任务线程，由**人类**执行提交动作；Agent 是否开放 submit_contract 工具随 E v1.2 修订定 | PRD"Agent 起草、人确认后生效"在单人 MVP 的最小落地；避免为确认流建状态机 |
| 7 | 画布结构编辑端点全员可用（C5），Agent 侧工具位默认**不开放**（O9 结构变更管控随 M6 提案流，M3 先收人类编辑面） | C5 vs O9 的里程碑切分：M3 无提案流，Agent 改图无管控载体，先关为安 |
| 8 | 节点删除确认弹窗文案（"解除引用不删任务"）是 UI 责任，端点无确认参数 | B §4.9 已注明 |
| 9 | M3 不实现 canvases 多画布：沿用 M1 预留的每频道一行 | C1"每频道一个画布页签" |

## 8. 挂账（勿当漏项重新发明；M2 移交 + 二轮 review 遗留）

| 出处 | 问题 | 归属 |
| --- | --- | --- |
| M2 挂账/二轮 review | 消息流附件卡数据源 = channelFiles 首页 ≤50（WS 失效已修，但旧文件仍缺）——需消息级 file 关联或 files 全量拉取 | **M3a 尾部顺手件**（消息域小重构） |
| M2 挂账 | id 游标分页 after 行离开结果集重发首页 + activity/files 无 SQL LIMIT 全量材料化 | keyset 统一整改，**独立小批**（messages/tasks/activity/files 四处同修） |
| 二轮 review 挂账 | ActivityScreen 三档缓存双请求（可簡化为 'all' 单拉 + 客户端过滤，连带删 wsBridge 多档 patch）；Mentions 徽标(未读)与列表(含已 done)口径差异为**有意设计**（列表灰显=历史） | 简化归 keyset 批或 M3 顺手；口径差异不改 |
| 二轮 review 挂账 | `_emit_activity` 在 routes 层——M4 升级类 activity（hub/reminder 路径）无法合理复用 | **M4 前迁 service 层**（M4 开工第一步） |
| 二轮 review 挂账 | hub.py usage.batch 逐事件 SELECT（批内可 IN 预查）；search 双 MATCH + LIKE 扫描 | 性能小批，量级小不阻塞 |
| M2 观察 | `task #n` refs 落库但无 UI 消费面（引用消息不渲染任务牌） | 画布/深链批顺手评估：refs → 消息内迷你 chip |
| M2 挂账 | P11 与 P3 看板双实现可抽 `<TaskBoard>` | B-M3-3 摸到看板 blocked 徽标时顺手评估 |
| M1 遗留 | pyright 109 既有错误 | 独立批处理，勿混 M3 业务提交 |
| M1 遗留 | 真实双 Agent OAuth 冷启动复验 | E6 真机场景可顺路执行，结论单独记录 |

## 9. M3 出口验收清单（按块分组；两块全绿即里程碑收口）

### 9a. 块 M3a「契约与校验」清单 —— **全绿（2026-07-10 收口）**

- [x] 1. Alembic `0003_m3` 落库：三表 + 约束（含 review 补的 `uq_task_contracts_active` 分区唯一 + `ix_task_contracts_task`）；从零与增量升级双路绿（E1）
- [x] 2. contracts 包 M3 登记完成：ENDPOINTS_M3(12) / 契约请求响应模型 / CONTRACT_BODY_MODELS / TASK_CONTRACT_KINDS / HANDOFF_REQUIRED_FIELDS，catalog 测试与 `pnpm gen` 两跑一致；MCP 工具组 M3 为空（E v1.2 裁决，已断言）；E 契约 v1.2 先行（E0）
- [x] 3. 契约提交/修订链全绿：新 revision 置换、superseded 终态、kind↔schema 校验、request-draft 直投（注入桩）+ 竞态 DB 兜底；loop_contract 不可挂 Task（E2）
- [x] 4. T7 逐路径验收：l2 缺契约/缺字段拒（missing 指名）、齐备放行、l1 直通、Agent 经 X-Acting-Member 同拒、升格绕过守护；M2 存量测试零回归（E3）
- [x] 5. P5 契约卡真数据：TaskPlan/TaskHandoff/revision/历史版本渲染、起草入口、HANDOFF_INCOMPLETE 缺失字段就地提示（B-M3-1）
- [x] 6a. 块 a 守门：后端 421 passed+3 skipped、web vitest 23、双侧 typecheck+build、ruff、gen 确定全绿；三处交接文档 + [M3A-EVIDENCE.md](../verify/M3A-EVIDENCE.md) 同步（纪律 5）

### 9b. 块 M3b「画布与 gating」清单（全绿 = **PRD M3 出口达成**）

- [ ] 7. 画布结构端点全绿：建节点=建 L2 任务+锚点消息（第三途径）、删节点保任务、无环拒 GRAPH_CYCLE、layout 不 bump 基线、baseline_advanced 广播（E4）
- [ ] 8. blocked 推导 + 投递 gating：上游未 done 不唤醒、done 解锁、force-start 仅人类 + 双留痕；状态写不受限（R4/R7 回归）（E5）
- [ ] 9. P2 画布：React Flow 可视化 + 实时着色 + 拖拽编辑（建节点/连断边/重指派）+ 成环预判 + 节点深链双向（B-M3-2）
- [ ] 10. 升格流：L1 拖入画布 → 补契约（Agent 起草/手填）→ level l1→l2；force-start UI 二次确认（B-M3-3）
- [ ] 11. **工程三角全流程真机实证**（PRD M3 出口）：六节点 DAG 建图 → blocked 标注与不唤醒 → 逐节点推进含 T7 门 → force-start 留痕 → 全程无刷新；录屏 + 截图归档 `docs/verify/`（E6）
- [ ] 12. 浮动件收口：FTS trigram 中文命中实测 + 结论回写 A §10.4（若明确移出 M3 须 owner 拍板记录）
- [ ] 13. 终收口守门：全量测试绿；M3 阶段结论写入 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本任务书移入 archive/（README 维护约定 3）

## 10. 第一步建议

块 M3a 从 **E0 + E1** 开工（契约登记 + 建表）：E0 把 E 契约 v1.2 修订收进 contracts 包让 manifest 回到"文档=代码"一致态（含 owner 拍板项：Agent 契约工具位、升格端点载体），E1 三表建齐是两块共同地基；同一会话顺手把一致性套件 M3 端点形状占位铺好（纪律 3）。B-M3-1 可即刻开工吃 mock（契约摘要 mock 形状 E0 补齐后）。**块 M3b 不与块 a 交错开工**——等块 a §9a 全绿再动，保持"一块 = 一个可交接的收口"。
