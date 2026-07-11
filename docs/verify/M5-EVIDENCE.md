# M5b / M5 出口实机 verify 证据（模板与向导 · PRD M5 出口）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-11 |
| 范围 | 块 **M5b**（H5 模板域 / H6 实例化事务器 / B-M5-2 前端）+ 并行审计修复 + 实机 verify-surfaced 修复；= **PRD M5 出口** |
| 环境 | 真 codex-cli **0.144.0**（ChatGPT 登录态）· 隔离临时库 alembic upgrade head（含 0007）+ seed · 真 uvicorn（create_app，lifespan upsert builtin 工程三角）· playwright 独立 chromium 1440×900 |
| 结论 | **codex 真机对话 PASS + M5b REST/daemon-sim 端到端 12/12 PASS（工程三角全管道走到人类终审 done）+ 5 前端截图（含实机发现并修复的 FR-7.3 warning bug）+ console 0** |

> 本证据为**重跑实测**：codex turn、REST 端到端探针、浏览器向导实例化均现场对真 codex CLI / 真 uvicorn / 真 chromium 运行。实机过程另外发现并修复 2 处单测漏网缺陷（§5）。

## 1. Codex 第二 runtime 真机完整 turn（PRD M5 出口的「实现=Codex」前置）

探针 `scratchpad/m5a_codex_turn2.py`：物化 `~/.codex/auth.json` 进隔离 `CODEX_HOME` → 起真 `codex app-server` → initialize → thread/start（sandbox=danger-full-access / approvalPolicy=never）→ turn/start。**2026-07-11 重跑**：

```json
{ "credentials_copied": ["auth.json"], "thread_started": true, "turn_completed": true,
  "turn_status": "completed", "agent_reply": "PONG", "reply_has_PONG": true,
  "usage": {"totalTokens":12126,"inputTokens":12120,"cachedInputTokens":9984,"outputTokens":6},
  "error_count": 0 }
=== M5a codex turn verify: PASS ===
```

codex 真回复 "PONG" + usage 提取 + 0 error → 第二 runtime 在本机真活。

## 2. M5b 端到端管道（真 uvicorn 8804；探针 `scratchpad/m5b_e2e_probe.py`）——**12/12 ALL PASS**

工程三角实例化到 research 频道（**实现=Codex Hank / 评审=Claude Rin / 产品=Pat / 人类=Memcyo**），
逐节点 claim → T7 handoff → in_review → done 走查（daemon-sim），gating 逐级解锁至人类终审 done。

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 创建 codex runtime Agent（POST /agents runtime=codex）→ 201 + runtime=codex | PASS |
| 2 | builtin 工程三角存在（lifespan upsert） | PASS |
| 3 | 实例化 201 + 落地批 done + kind=tmpl | PASS |
| 4 | 6 任务（L2）落地 | PASS |
| 5 | 画布 6 节点 5 边（research 空画布精确计数） | PASS |
| 6 | 每任务带 TaskPlan 初稿（plan_skeleton 落契约） | PASS |
| 7 | 任务起始 owner=None（待认领，role_mapping 只设 created_by） | PASS |
| 8 | briefing 系统消息 @Codex(Hank) + @Claude(Rin)（唤醒信号） | PASS |
| 9 | gating 初态：首节点(需求框定)不 blocked、评审门 blocked | PASS |
| 10 | 幂等重放（同 Idempotency-Key）→ 同批同任务、画布无重复节点 | PASS |
| 11 | 全管道走查到 done（6 节点 claim/T7 handoff/in_review/done，逐级解锁） | PASS |
| 12 | 6 任务全 done + 人类终审 done | PASS |

管道走查逐节点（owner runtime · claim/handoff/in_review/done 全 200/201 · gating 前 blocked=False，done 后下游解锁）：

```
PASS  需求框定  owner=claude claim=200 handoff=201 in_review=200 done=200  next_unblocked=True
PASS  评审门    owner=claude claim=200 handoff=201 in_review=200 done=200  next_unblocked=True
PASS  实现契约  owner=codex  claim=200 handoff=201 in_review=200 done=200  next_unblocked=True
PASS  TDD 实现  owner=codex  claim=200 handoff=201 in_review=200 done=200  next_unblocked=True
PASS  独立验收  owner=claude claim=200 handoff=201 in_review=200 done=200  next_unblocked=True
PASS  人类终审  owner=human  claim=200 handoff=201 in_review=200 done=200  next_unblocked=None
=== M5b H7 e2e verify: 12/12 ALL PASS ===
```

坐实 PRD M5 出口链：**工程三角向导实例化（实现=Codex、评审=Claude）→ briefing @角色开工 → Codex
交付（claim+handoff）→ Claude 评审（claim+handoff）→ 人类终审 done**，全程 gating 逐级放行；第二
runtime 真活见 §1。

## 3. 前端向导实机（playwright 1440×900，console 0 错误）——真 chromium 走完整向导实例化

| 截图 | 内容（已逐张核验渲染） |
| --- | --- |
| [m5b-tmpl-menu.png](m5b-tmpl-menu.png) | 画布工具栏「模板▾」菜单：存为模板（gating 可用）/ 从模板新建… |
| [m5b-wizard-step1.png](m5b-wizard-step1.png) | 向导步①选模板：工程三角 builtin 卡 + DAG 缩略图（6 节点线性）+「6 节点 · 4 角色」 |
| [m5b-wizard-step2-warn.png](m5b-wizard-step2-warn.png) | 向导步②角色映射：实现→Pat(claude)、评审→Rin(claude) **同 runtime → FR-7.3 warning 触发**（实机修复后，见 §5）；实现工程师正确标「实现」 |
| [m5b-wizard-step3-preview.png](m5b-wizard-step3-preview.png) | 向导步③预览：DAG 缩略图 + 完整 briefing + 角色→成员摘要（实现→Hank、评审→Rin、人类→待认领） |
| [m5b-canvas-instantiated.png](m5b-canvas-instantiated.png) | **向导真实例化后画布**：6 节点线性 DAG（需求框定→评审门→…→人类终审）左→右分层 5 边；#1 不 blocked、#2–#6「blocked · 等待上游」；画布/看板徽标 6（实机修复分层布局后，见 §5） |

> 向导→实例化全程 WS 事件驱动无刷新；B-M5-2 前端逻辑另由 vitest（TemplateWizard / SaveTemplateModal / templates / SetupChecklistScreen）覆盖。

## 4. 并行审计（4 agent 前置于实机）→ 1 blocking + 1 major + 若干 minor 全处理（提交 `42b7b64`）

- **[BLOCKING] 实例化幂等 reserve-before**：原 `record()` 在副作用之后 → 并发同键各建一批（重复落地批）。改为建节点前先 `record({batch_id})`（照 messages.py 先例）；因 ApiError 有处理器**不回滚**事务，所有可失败校验（404/422）全部前置于 reserve 之上，reserve 后 instantiate 不再抛错。op_id payload 只记 batch_id，task_ids 由 create_node 账本行派生。
- **[MAJOR] 向导死控件**：「＋新建 Agent…」两 caller 均未接 → 空操作 + awaitingCreateFor 卡死致后续新增 agent 误映射。删除该控件与回填机制。
- **[MINOR]** role_mapping 未知成员落库前 422（防 FK 500）· instantiate 顶部 re-run validate_template_body（悬挂 edge 由 500→422）· briefing 空正文+无 mention 跳过发消息 · 前端 instantiate 发 Idempotency-Key（网络重试不重复建批）· 422 details.missing 入 toast。
- 测试补齐：SetupChecklist 003 接真 + 向导 step3 预览内容 + 后端 unknown_member_422 / failed_precheck_leaves_no_op_id。

## 5. 实机 verify-surfaced 修复（单测漏网，实机现形）

| # | 缺陷 | 实机现象 | 修复 | 回归测试 |
| --- | --- | --- | --- | --- |
| 1 | **FR-7.3 同 runtime 互审 warning 对 builtin 工程三角失效** | 向导步②把实现→Pat(claude)+评审→Rin(claude) 同 runtime，**warning 不触发**；实现工程师被误标「评审」 | `classifyRole` 原并入 description 判定——builtin「实现工程师」description 含「交独立验收」被误归 review，致无 implement 角色可比。改为**仅按占位名判定**（占位名是角色身份权威信号；用户模板占位=成员名→other，警示自不触发） | `templates.test.ts`：doer 的 description 含「验收」仍归 implement；产品负责人不误归 review |
| 2 | **实例化节点全堆叠原点** | 画布只见一个节点（6 节点 pos 全 (0,0)） | `instantiate_template` 加最长路径分层布局（镜像向导 DAG 缩略图 Kahn 分层），节点左→右分层排开 | `test_templates.py::test_instantiate_layout_spreads_nodes`（线性 DAG → 6 个不同 x 层） |

单测用干净占位/未断言坐标故漏网——实机浏览器一眼现形，是 verify 存在的意义。

## 6. 守门基线（M5b + 全部修复收口态）

```
uv run pytest -q          → 710 passed / 4 skipped
pnpm -F @coagentia/web test → vitest 174 passed
pnpm typecheck            → pyright 0 + 双 tsc 0
uv run ruff check .       → 干净
pnpm gen                 → diff 空（未触 contracts）
pnpm -F @coagentia/web build → 绿
```

## 7. 探针脚本（scratchpad，可复跑）

- `m5a_codex_turn2.py`：codex 真机 turn（凭证物化 + PONG + usage）。
- `m5b_e2e_probe.py`：M5b REST/daemon-sim 端到端 12 checks（实例化 → gating → briefing → 幂等 → 全管道走到人类终审 done）。
- `m5a_shot_server.py`：前端截图 server（seed + 真机探测 runtimes 注入 + dist）。
