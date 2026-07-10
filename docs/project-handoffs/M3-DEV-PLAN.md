# M3 执行计划（M3-DEV-PLAN）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-10，M3 开工首会话（任务书 [M3-HANDOFF.md](archive/M3-HANDOFF.md)） |
| 用途 | M3 逐模块执行计划：步骤分解、会话/agent 切分、测试策略、验收映射、进度表（体例同 [M2-DEV-PLAN.md](M2-DEV-PLAN.md)） |
| 本批范围 | **块 M3a「契约与校验」**（E0·E1·E2·E3 ＋ B-M3-1；出口清单 §9a）。块 M3b 不与 a 交错，等 §9a 全绿再另开 |
| 编排方式 | 多 agent 工作流（自编排）：地基阶段 E0 ∥ E1 并行 → 后端 E2→E3 串行 ∥ 前端 B-M3-1 并行 → 整合守门 → 实机 verify → `/code-review high` |

---

## 0. 开工拍板项（owner 可否决，否决处升契约版本回改）

| # | 拍板项（M3-HANDOFF §7.6/E2 遗留） | 裁决 | 依据 |
| --- | --- | --- | --- |
| P-1 | Agent 契约工具位（submit_contract 是否开放） | **不开放**：M3 契约面零新 Agent 工具。契约读走已有 `get_task` 摘要；起草经 `request-draft` S1 直投 + 现有 `send_message` 贴任务线程；提交由**人类** POST contracts。已落 **E 契约 v1.2**（engineering_docs/05 §3 M3 工具组裁决表 + 变更记录） | PRD §4.3「Agent 起草、人确认后生效」单人 MVP 最小落地；M3-HANDOFF §7.6 裁决 6、适配器 §3 line 62「契约提交不开放 Agent」 |
| P-2 | 升格端点载体（独立端点 vs PATCH 扩展） | **扩展 `PATCH /tasks/{id}`** 接受 `level` 字段：仅 `l1→l2` 单向；`l2→l1` 或非法值 → 422 `TASK_TRANSITION_INVALID`（rule=D1；details `{from, to}`）。升格是任务字段变更，PATCH 已存在且不写 task_events（无 promotion 事件 kind），避免为单字段建端点 | M3-HANDOFF §7.4 升格单向 l1→l2；契约 A tasks.level 定义；PATCH 语义（不写 task_events，广播 task.updated） |
| P-3 | request-draft 目标 Agent 离线行为 | daemon 未连该 Agent → **503 DAEMON_OFFLINE**（同 lifecycle/home 定向 daemon 交互先例，S1 直投不参与对账补发，契约 D §4.3）；连接则发 `message.inject` 帧后 202 | 与既有定向 daemon 端点一致；直投是 best-effort 非积压 |

> P-1/P-2/P-3 均属"实现按此执行、owner 可否决"档；无阻塞，若 owner 否决 P-1/P-2 处升 E/B 契约版本回改。

## 1. 现有资产复用锚点（已 scout 核实，勿重建）

- 三表实体形状 `packages/contracts/entities.py:370-437` 已冻结；manifest 测试 `test_manifest_entities.py:71-83` 已列 task_contracts/canvas_nodes/canvas_edges 字段——E1 建表照此。
- 契约 body 模型 **全仓不存在**（enums 只有 `ContractKind`）——E0 按 PRD §4.3 v1 schema 新建 TaskPlan/TaskHandoff/LoopContract Pydantic 模型。
- 错误码 `HANDOFF_INCOMPLETE`/`GRAPH_CYCLE` 等已在 `rest.py:36` ErrorCode（22 码），E0 无需增码。
- `MessageInjectData` + `InjectKind.CONTRACT_DRAFT_REQUEST` 已在 `daemon.py:192`/`enums.py:248`——request-draft 直投载体现成。
- 路由经 `request.app.state.daemon_hub` + sync 桥（`send_lifecycle` 范式，`members.py:245`）——E2 加 hub inject 方法同构。
- 任务端点/service 范式 `routes/tasks.py` + `tasks/service.py`（write_event/emit_task_updated/TASK_TRANSITIONS 消费）——E2/E3 扩此。
- 一致性双跑 `test_conformance_dual.py`（M2 端点已扩，M3 照扩）。
- P5 契约折叠卡骨架 `apps/web/src/screens/ThreadPanel.tsx:119-141`（`contracts.length>0` 分支 + "暂无契约(M3 接入)"占位）——B-M3-1 接真为主。
- gen 管线：`pnpm gen`（export_schemas.py 自动收集 rest.py 全部 ContractModel + mock openapi + constants.json）——E0 加模型后重生成、diff 守门。

## 2. 模块分解与 DoD

### E0 · 契约登记同步（块 M3a 地基，**开工第一步**）— 文件：packages/contracts + mock-server + build/*

1. `rest.py` 新增契约 body 模型（PRD §4.3 v1，带字段校验）：
   - `TaskPlanBody`（version 定值 `coagentia.task-plan.v1`；`goal:str`；`acceptance_criteria: list[AcceptanceCriterion]` ≥1；`AcceptanceCriterion{id, statement, verify_by: VerifyBy, verify_ref}`；`defaults_decided:[str]`；`out_of_scope:[str]`）。
   - `TaskHandoffBody`（version `coagentia.task-handoff.v1`；`from_member/to_member: Ulid`；`deliverables: list[Deliverable]=[]`；`Deliverable{path, kind: DeliverableKind}`；`evidence: list[Evidence]=[]`；`Evidence{type: EvidenceType, ref, conclusion}`；`open_risks:[str]`；`verify_plan:str`）。**deliverables/evidence 提交期允许空**（handoff 可增量起草）；PRD §4.3「≥1」由 **T7 流转门**在置 in_review 时执法，非 body 提交门——否则"缺 deliverables 拒"这条 T7 路径不可达（实现时已按此修正 E0 初版的 min_length）。
   - `LoopContractBody`（version `coagentia.loop-contract.v1`；`cadence`；`verification:[str]`；`budget: LoopBudget{max_retries=1, max_runtime_min}`；`tools:[str]`；`escalation:str`）——**表形状就位，生成归 M4，但模型 M3 建齐**。
   - 新枚举（enums.py）：`VerifyBy(command/inspect/manual)`、`DeliverableKind(file/dir/url/artifact)`、`EvidenceType(test/command/screenshot/log)`。
2. 契约端点请求模型：`ContractCreate{kind: ContractKind, body: JsonValue}`（body 按 kind 二次 model_validate 归 server）、`ContractDraftRequest{kind: ContractKind, agent_member_id: Ulid}`；`TaskPatch` 增 `level: TaskLevel | None`（P-2）。
3. `rest.py` `ENDPOINTS_M3` 元组（B §4.7 契约组 + force-start + §4.9 画布组，**画布组块 a 期间登记但不 serve**，force-start 归 E5）：
   `GET/POST /tasks/{task_id}/contracts`、`POST /tasks/{task_id}/contracts/request-draft`、`POST /tasks/{task_id}/force-start`、`GET /channels/{channel_id}/canvas`、`POST /canvases/{canvas_id}/nodes`、`PATCH/DELETE /canvases/{canvas_id}/nodes/{node_id}`、`POST /canvases/{canvas_id}/edges`、`DELETE /canvases/{canvas_id}/edges/{edge_id}`、`PUT /canvases/{canvas_id}/layout`、`POST /canvas-nodes/{node_id}/retry`。
4. catalog 测试（`test_catalogs.py`）：`ENDPOINTS_M3` 计数 + 与 M1/M2 不相交断言；MCP 目录**不扩**（M3 组空，E v1.2 裁决），加一条断言注明。
5. mock-server（`apps/mock-server/app.py`）补 M3 读端点形状：`GET /tasks/{id}/contracts` → `list[TaskContractPublic]`（空）、`POST` 系列返回对应 Public 形状（纪律 4：形状源非逻辑源，无环/T7/revision 只活真 server）；TaskDetail contracts 仍空。
6. `pnpm gen` 重生成（contracts.schema.json + openapi.json + constants.json），入仓 diff 为空。
- **DoD**：manifest/catalog 测试红转绿；`pnpm gen` diff 为空；mock 一致性扩展全绿；contracts + web-ts typecheck 绿。

### E1 · M3 建表迁移（块 M3a 地基）— 文件：apps/server/db/models.py + migrations/versions/0003_m3.py

1. `models.py` 新增 SQLAlchemy 模型：`TaskContract`（task_contracts；CHECK task_id/reminder_id 恰一非空；活动 revision 语义按 A §4.3——修订新增行、旧行 superseded_at）、`CanvasNode`（canvas_nodes；CHECK kind=agent→task_id NOT NULL、kind=system→system_action NOT NULL；task_id UNIQUE；REAL pos_x/pos_y）、`CanvasEdge`（canvas_edges；UNIQUE(canvas_id,from,to)、CHECK from≠to）。字段/类型对齐 manifest。
2. `M3_TABLES = ("task_contracts", "canvas_nodes", "canvas_edges")`；三表一次建齐（canvas 两表块 a 空置，迁移不拆两次——裁决 1）。
3. `0003_m3.py`：范式照抄 `0002_m2.py`（显式点名 `_m3_tables()` create_all，避免 create_all 读全集坑1）；无新不可变表（task_contracts 可 UPDATE superseded_at，非不可变）。
- **DoD**：从零 `alembic upgrade head` 与 M2 库增量升级双路绿；表结构对照测试扩展（新增迁移测试断言三表建成 + 关键约束）；server typecheck 绿。

### E2 · 契约域端点（块 M3a）— 文件：apps/server/routes/tasks.py + tasks/service.py（或新 contracts/service.py）+ computers/hub.py + serialize.py

1. `GET /tasks/{task_id}/contracts` → `list[TaskContractPublic]`（该任务全部 revision，或仅活动？按 B §4.7「契约读取」返回全部含 superseded，前端按 superseded_at 区分活动/历史）。
2. `POST /tasks/{task_id}/contracts`（提交与修订）：`ContractCreate` body 按 kind `model_validate`（kind≠schema 或校验失败 → 422 VALIDATION_FAILED）；同 (task_id, kind) 已有活动行 → 新 revision（revision=旧+1）、旧行置 superseded_at（同事务）；WS `task_contract.created`（首版）/`task_contract.updated`（修订）发射；superseded 行不可再改（修订只作用最新活动行）。
3. `POST /tasks/{task_id}/contracts/request-draft`（`ContractDraftRequest`）：校验 task 存在 + agent_member_id 是 agent 成员 → hub inject `message.inject`（source=contract_draft_request，body=起草请求渲染文本）→ 202；daemon 离线 → 503 DAEMON_OFFLINE（P-3）。hub 加 sync 桥 `inject_contract_draft_request`（范式同 send_lifecycle）。
4. `PATCH /tasks/{id}` 扩 `level`（P-2）：l1→l2 放行、l2→l1/非法 → 422 TASK_TRANSITION_INVALID(rule=D1)；不写 task_events、广播 task.updated。
5. `get_task_detail` 的 contracts 接真（查该任务活动契约 → TaskContractPublic 列表，替换 M2 恒空 `[]`）；serialize 加 `task_contract_public`。
- **DoD**：一致性双跑扩契约端点（GET/POST 形状）；修订链测试（新 revision 置换、superseded 终态不可改、kind≠schema 拒）；request-draft 直投帧断言（hub 注入桩 spy）；升格 l1→l2 放行 / l2→l1 拒；M2 全量回归零红。

### E3 · T7 流转校验（块 M3a）— 文件：apps/server/routes/tasks.py（set_task_status）+ tasks/service.py

1. `set_task_status` 追加 T7 门：`task.level == l2` 且 `to == in_review` → 校验该任务**活动**（未 superseded）最新 `TaskHandoff` 的 deliverables/evidence 非空 → 缺则 422 HANDOFF_INCOMPLETE `details{missing: [...]}`（无 handoff 视同缺 deliverables+evidence，裁决 5）；人与 Agent（X-Acting-Member）同拒。
2. l1 任务不受影响（M2 存量全 l1，行为回归保障）；T7 必填字段清单走 contracts 常量（纪律 7，如 `HANDOFF_REQUIRED_FIELDS`）供 server 校验 + 前端提示同源。
- **DoD**：逐路径测试——l2 无契约拒 / 缺 deliverables 拒（missing 指名）/ 缺 evidence 拒 / 齐备放行 / l1 直通 / Agent 经 X-Acting-Member 同拒；M2 384+ 存量测试零回归。

### B-M3-1 · P5 契约卡接真（块 M3a）— 文件：apps/web/src/screens/ThreadPanel.tsx + api 层 + vitest

1. 契约折叠卡消费真 `detail.contracts`：TaskPlan（goal + AC 列表含 statement/verify_by/verify_ref）、TaskHandoff（deliverables path/kind + evidence type/ref/conclusion）、revision 徽标（>1 显示 rev n，superseded 历史灰显）。
2. "让 @Agent 起草"入口：调 `POST /tasks/{id}/contracts/request-draft`（选 Agent + kind）→ 202 toast。
3. T7 拒绝：置 in_review 命中 HANDOFF_INCOMPLETE → 就地提示缺失字段（"缺少：deliverables(0)/evidence(0)"，交互 §5.4）。
4. l1 任务契约卡保持"暂无契约"但**去掉"M3 接入"**文案。
- **DoP**：行为测试（vitest）+ playwright 1440×900 屏对照（消费归档稿 previews/ 若有契约卡稿）；token 零发明。

## 3. 会话/agent 切分（自编排工作流）

| 阶段 | agent | 文件域（互不重叠） | 依赖 |
| --- | --- | --- | --- |
| 地基（并行） | **A-E0** 契约登记 | packages/contracts、mock-server、build/* | 无 |
| 地基（并行） | **A-E1** 建表迁移 | server/db/models.py、migrations/0003 | 无（实体形状已冻结） |
| — barrier：地基双绿（contracts/catalog/manifest + migration 测试 + pnpm gen diff 空）— | | | |
| 实现（并行） | **A-E23** 后端契约端点+T7 | server/routes/tasks.py、tasks/service.py、contracts service、hub.py、serialize.py、server tests | E0 ∧ E1 |
| 实现（并行） | **A-BM31** 前端契约卡 | apps/web/src、web tests | E0（mock 形状） |
| — barrier：整合守门（全量 pytest + vitest + 双侧 typecheck + ruff + pnpm gen diff 空）— | | | |
| 收口（主 loop 亲为） | 实机 verify（真 server HTTP + 浏览器契约卡/T7）→ `/code-review high` 修复 → 三处交接文档同步 | — | 全绿 |

- 切分依据：E0（契约包）与 E1（server 建表）文件域完全不相交 → 真并行；E2/E3 同改 routes/tasks.py 故同 agent 串行；前端 apps/web 与后端 apps/server 不相交 → 并行。
- 纪律 8（图结构语义单一事实源）本批不触（无环/blocked 属 M3b）；纪律 7 延伸：T7 必填字段清单、契约 kind↔schema 映射走 contracts 常量/模型。

## 4. 测试策略

- **契约层**：catalog（ENDPOINTS_M3 计数/不相交、MCP 目录不扩）、manifest（三表已覆盖）、body 模型校验（≥1 约束、version 定值、kind↔schema）。
- **迁移层**：从零 upgrade head + M2 增量升级双路；三表建成 + 关键约束（恰一非空/UNIQUE/CHECK）断言。
- **端点层（一致性双跑扩 `test_conformance_dual.py`）**：契约 GET/POST 形状、TaskDetail contracts 接真形状。
- **业务层（真 server 独有）**：修订链、request-draft 直投帧（注入桩）、T7 逐路径、升格单向、Agent 经 MCP 同拒。
- **前端**：vitest 行为（契约卡渲染/起草入口/T7 缺失提示）+ playwright 屏对照。
- **回归**：M2 全量 387 passed 零红；`pnpm gen` 确定性 diff 空。

## 5. 验收映射（→ M3-HANDOFF §9a）

| §9a 清单 | 本计划模块 |
| --- | --- |
| 1. Alembic 0003 三表双路绿 | E1 |
| 2. contracts M3 登记 + gen diff 空 + E v1.2 先行 | E0（E v1.2 已落 engineering_docs/05） |
| 3. 契约提交/修订链 + kind↔schema + request-draft 直投 | E2 |
| 4. T7 逐路径 + M2 零回归 | E3 |
| 5. P5 契约卡真数据 + 起草入口 + HANDOFF_INCOMPLETE 就地提示 | B-M3-1 |
| 6a. 块 a 守门：pytest+vitest+双 typecheck+ruff 全绿 + 三处文档同步 | 收口阶段 |

## 6. 进度表（块 M3a 收口 2026-07-10）

| 模块 | 状态 | 备注 |
| --- | --- | --- |
| E 契约 v1.2 修订 | ✅ 完成 | engineering_docs/05 §3 M3 工具组裁决表 + header + 变更记录 |
| E0 契约登记 | ✅ 完成 | enums 三枚举 + rest.py body 模型/ContractCreate/ContractDraftRequest/ENDPOINTS_M3(12)/CONTRACT_BODY_MODELS；mock GET contracts 形状；catalog 测试；pnpm gen 确定 |
| E1 建表迁移 | ✅ 完成 | 0003_m3 三表（task_contracts/canvas_nodes/canvas_edges）+ M3_TABLES；从零/增量双路绿；schema-conformance 扩 |
| E2 契约端点 | ✅ 完成 | GET/POST contracts（修订链+kind↔schema）、request-draft（S1 直投/503）、PATCH level 升格、TaskDetail 接真、hub inject 桥 |
| E3 T7 校验 | ✅ 完成 | set_task_status 追加 l2→in_review handoff 非空门；逐路径测试；M2 零回归 |
| B-M3-1 契约卡 | ✅ 完成 | ThreadPanel 真渲染 TaskPlan/TaskHandoff/rev/历史；起草入口；T7 就地提示；vitest（引入 happy-dom+testing-library） |
| 整合守门 | ✅ 后端 421 passed+3 skipped、web typecheck/build/vitest 23、gen 确定、ruff 干净 |
| 实机 verify | ✅ 真 HTTP 16/16 + 2 新守卫 + 浏览器契约卡/T7 就地提示截图（[M3A-EVIDENCE.md](../verify/M3A-EVIDENCE.md)） |
| /code-review high | ✅ 8 角度 finder → CONFIRMED 全修（6 正确性+3 质量+回归测试），复核实机通过（同证据 §D） |

**块 M3a 出口（§9a 清单）全绿——里程碑前半完成。块 M3b（画布与 gating）不与 a 交错，另开会话按 M3-HANDOFF §5/§9b。**

## 7. 进度表（块 M3b 收口 2026-07-10）

编排方式：多 agent 工作流（自编排）——**E0b 契约地基先行** → 三轨并行（**后端 E4→E5 串行 ∥ 前端 B-M3-2→B-M3-3 串行 ∥ FTS trigram**，文件域不相交）→ 守门汇总 → 实机 verify（主 loop 亲为）→ `/code-review high`（8 角度工作流）→ 修复 → 复跑。

| 模块 | 状态 | 备注 |
| --- | --- | --- |
| E0b 契约登记 + 图内核 | ✅ 完成 | rest.py canvas 请求/响应模型（CanvasDetail/NodeCreate/NodePatch/EdgeCreate/LayoutPut/CanvasMutation）；`kernel/graph.py`（detect_cycle/derive_blocked，权威）+ `golden/graph.json` 判例；mock GET canvas 读形状；pnpm gen 确定 |
| E4 画布结构端点 | ✅ 完成 | `routes/canvas.py` + `canvas/service.py`：快照/nodes CRUD（agent 节点建 L2 任务+锚点消息=第三途径）/edges CRUD（写事务拓扑排序，成环 GRAPH_CYCLE）/layout PUT（不 bump）；每写串行化点 + baseline bump + baseline_advanced 广播；目录 vs 实 serve 一致性测试（retry 归 M6 不 serve） |
| E5 blocked gating + force-start | ✅ 完成 | blocked 实时推导（图内核，不落库）；两处投递决策点门（`_deliver_message` + `reconcile`，作用于投递批含水位截断）；force-start（仅人类 403 C3、双留痕、不改状态、hub 桥本次放行）；R4/R7 状态写不受限 |
| B-M3-2 画布页签 | ✅ 完成 | React Flow（@xyflow/react）：节点=任务卡实时着色 + blocked 级联 + 系统菱形；连边成环 TS 预判 + 服务端复核；断边/布局防抖 PUT（拖拽中不回弹坐标）；深链 ?node= 双向；`lib/graph.ts` 镜像 + golden 对照；wsBridge 7 个 canvas.* handler |
| B-M3-3 升格/force-start UI | ✅ 完成 | 新建节点弹层含契约区（Agent 起草/手填）；ForceStartModal 二次确认（P13b 范式）；看板 P3/P11 blocked 徽标（`blockedTaskIdsFromCanvas` 共享 `lib/graph.deriveCanvasBlocked`） |
| FTS trigram（浮动件） | ✅ 完成 | `0005_fts_trigram` 迁移（unicode61→trigram，双路+downgrade）；<3 字 CJK 走正文/锚点 LIKE 兜底（元字符转义）；契约 A §10.4 回写「3 字符地板」结论 |
| 整合守门 | ✅ 后端 **483 passed+3 skipped**、web vitest **76**、pyright 0（并入 typecheck）、ruff 干净、gen 确定、双侧 build 绿 |
| 实机 verify | ✅ 真 uvicorn 8799 + 真 websockets daemon-sim **17/17**（六节点 DAG/成环拒/T7/force-start 403·留痕/blocked 不唤醒·解锁·force-start 唤醒无死锁/R4·R7）+ 浏览器同源 6 截图（含 **WS 无刷新实时解锁**）+ console 0 错误（[M3B-EVIDENCE.md](../verify/M3B-EVIDENCE.md)） |
| /code-review high | ✅ 8 角度 finder → 24 候选 → **10 CONFIRMED 全修 + 4 回归测试**：gating 投递批泄漏（过滤 gated+水位截首个 gated 前，不消费）· patch_node V14 复校 · LIKE 元字符转义 · tasks 锚点 <3 字兜底 · blocked_task_ids 收窄单画布 · 拖拽 WS 回弹 · satisfied 前后端抽 graph.ts · wsBridge helper 去重；复跑守门 + E6 实机 17/17 复证 |

**块 M3b 出口（§9b 清单 7–13）全绿——PRD M3 出口达成，里程碑收口。**
