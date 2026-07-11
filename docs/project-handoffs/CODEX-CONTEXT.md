# CODEX-CONTEXT —— 交接任务的完整项目上下文

> **读者**：接手本仓库开发任务的 AI 工程师（Codex CLI）。本文自足——假设你没有任何先前会话记忆，所有内部术语在此解码。读完本文再按 §4 阅读顺序进入具体任务。
> **维护**：本文描述的是 2026-07-11（M6 立项、HEAD `562af66`）时点的事实；动态进度以 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) 与 [M6-DEV-PLAN.md](M6-DEV-PLAN.md) 为准。

---

## 1. 项目是什么

**CoAgentia**（曾名 AgentHub，2026-07-09 改名）：运行在**用户本机**的多 Agent 协作 IM——人类和多个 AI Agent 像同事一样在频道里聊天、领任务、交付代码。形态类似 Slack + 看板 + 流程画布，但成员一半是 AI。

- **单机单人 MVP**：Windows 11 本机部署、单人类用户、SQLite 存储、无云端。安全模型 = 全信任（Agent 以 bypassPermissions 驱动，对本机有执行权——owner 已拍板接受，PRD NFR5）。
- **产品哲学**：判断归模型、控制归代码——LLM 只产出提案/消息，一切校验、状态机、幂等、留痕由确定性代码执行。消息不可变 + 全留痕 = 聊天记录即事实账本。
- **开发模式**：契约驱动。六份技术契约（§4）是唯一权威，**实现是契约的填空**；改行为必须先改契约（升版本+变更记录），再改代码。

## 2. 核心概念速览（内部黑话解码）

### 2.1 基座（M1 交付）

| 术语 | 含义 |
| --- | --- |
| 工作区/频道/DM/线程 | Slack 同构；**消息不可变**（无编辑/删除，只有新增） |
| 成员 | 人类 + Agent 两种；Agent 有 5 态生命周期（starting/idle/busy/error/offline） |
| Computer / daemon | Agent 跑在某台机器的 daemon 进程里；daemon 通过 WS 连 server，五职责=保连接/跑 Agent/管进程/投递消息/跑交付进程。**daemon 是执行器不是决策者**——一切判定在 server |
| runtime / 适配器 | Agent 的驱动引擎：`claude`（Claude Code CLI，契约 E）或 `codex`（Codex CLI app-server，契约 E2）；适配器把 runtime 进程封装成统一接口 |
| 唤醒/投递 | server 判定谁该被唤醒（新消息/@mention/reminder/画布激活四触发），daemon 执行投递；投递 ack 后 server 写已读游标 |
| S1 直投 | `message.inject` 帧：定向发给单个 Agent、**不进频道流**、不动已读游标（修复循环/护栏反馈用） |
| 对账（reconcile） | daemon 重连后 server 按 DB 事实源推导补发指令（无指令 outbox）——离线期间该发生的事补齐恰一次 |

### 2.2 任务与契约（M2/M3 交付）

| 术语 | 含义 |
| --- | --- |
| 任务 5 态 | todo → in_progress → in_review → done / closed（合法边表 `TASK_TRANSITIONS` 单一事实源） |
| claim | 认领任务（条件 UPDATE 防重，同刻唯一 owner） |
| L1/L2 | 契约分级：L1 轻任务只有元数据；L2 正式交付强制 schema 契约 |
| TaskPlan / TaskHandoff / LoopContract | 三种 L2 契约：计划（goal+验收标准）/ 交接（交付物+证据，**T7 门**=置 in_review 时校验非空）/ 循环任务上岗契约 |
| 升格 | L1 任务拖入画布时补契约升 L2 |
| 画布（canvas） | 每频道一张 DAG：节点=任务（React Flow 渲染），边=依赖；成环拒绝（GRAPH_CYCLE）；结构变更 bump 基线版本+指纹（CAS 用） |
| blocked / gating | 上游未 done 的节点 blocked（**实时推导不落库**）；gating 作用于**投递层双面**——blocked 任务线程的消息既不触发唤醒、也从投递批中剔除且已读水位不越过（历史教训：只 gate 唤醒会被兄弟消息顺带消费） |
| force-start | 人类无视 blocked 强制放行一次（Agent 403，双留痕） |

### 2.3 护栏与提醒（M4 交付）

| 术语 | 含义 |
| --- | --- |
| freshness / HeldDraft | Agent 发消息前若 scope 内有未读新消息 → 草稿被"扣住"（held），202 不落库；人类三键干预=放行/丢弃/带新上下文重评估；G4 超时自动重评估、G5 升级喊人 |
| 沉默提醒（D5） | 任务长时间无活动 → 按状态三级阈值提醒 → 升级 |
| Reminder | 一次性/循环（interval 或 cron 五段式）；**塌缩式重排**（停机漏 K 个周期只补一次，防重放风暴——历史教训） |

### 2.4 模板与落地批（M5 交付）

| 术语 | 含义 |
| --- | --- |
| 模板/向导 | 画布流程存为模板（TemplateBody: nodes/edges/roles/briefing），向导三步实例化；**工程三角** builtin 模板=6 节点线性 DAG（需求框定→评审门→实现契约→TDD 实现→独立验收→人类终审），评审与实现默认异 runtime 互审（checker≠doer） |
| landing batch / ledger | **通用幂等账本**：批量落地操作（建任务/节点/边）逐条携 `op_id`+`request_hash` 写 `ledger_entries`；同键同指纹=跳过、同键异指纹=**fail-closed** 停批告警、批次无 `:done` 标记=重放补齐。`tmpl:<batch_id>:…` 是第一个消费者（模板实例化），M6 的 `decomp:`/`delta:` 走同一套 |

### 2.5 M6 要建的（你的任务域）

| 术语 | 含义 |
| --- | --- |
| Project | 频道绑定的本机 git 仓库 + dev/deploy 命令配置 |
| worktree 分级派生 | writes_code=1 的 L2 画布任务激活时自动 `git worktree add` 独立工作树（并行任务不踩脚）；只读任务不派生；任务终态后按保留天数清理 |
| 工作目录注入 | Agent 如何知道去 worktree 干活：**server 在唤醒/简报消息里注入绝对路径话术**（owner 拍板；适配器零修改） |
| Diff 卡 | 交付卡上的分支 diff（daemon `git diff` 代理上报 DiffPayload，逐文件 patch） |
| 系统节点 | 画布上的非 Agent 节点（W8）：`merge`（按 DAG 序 `git merge --no-ff` 合入主干）/ `check`（主工作区跑校验命令）；状态 idle→running→success/failed，仅 failed 可 retry；表列与创建路径 M3b 已建好，**M6 只补执行面** |
| 冲突派回 | merge 冲突不静默：abort 恢复主干 + 自动建"解决冲突"任务派回原 owner |
| Orchestrator | 内置角色模板的**成员 Agent**（数据不是代码）：@它一句话需求 → 产出 DecompositionProposal（拆解提案） |
| `<control>` 块 | 提案输出协议：消息正文给人读，机读 JSON 放唯一 `<control>{...}</control>` 块，server 解析（散文里的承诺不构成系统输入） |
| V1–V14 | 提案确定性校验规则（schema 层+语义层，错误全量收集）；**同构内核**：py 权威 + ts 镜像 + golden 判例双跑 |
| 修复循环 | 校验失败错误清单 S1 直投退回 Orchestrator 自动修复重提，≤2 次穷尽升级人类 |
| 草稿画布 | 提案渲染为半透明草稿层（不落画布表），人类拖拽调整→确认落地（CAS 指纹防"批的和执行的不是一份"）；频道可配"直落"跳过确认 |
| delta | 落地后的增量结构变更提案（base 指纹防过时基线；人类可逐项剔除=部分接受） |
| O9 拦截 | Agent 不得直接调画布结构写端点（403），结构变更唯一通道=`<control>` delta 提案；人类不受限 |

## 3. 技术栈与仓库地图

**栈**：Python 3.14 + FastAPI + SQLAlchemy 2.0 + Alembic + SQLite（**须 ≥3.35**，用 RETURNING）/ TypeScript + React + Vite + React Flow + vitest / monorepo = pnpm workspace + uv。

```
D:\Project4work\Agenthub_7_8\
├── coagentia\                      ← git 仓库（main，无 remote，全部提交仅本地）
│   ├── apps\server\                ← FastAPI 服务端（核心）
│   │   ├── src\coagentia_server\
│   │   │   ├── routes\             ← REST 端点（channels/messages/tasks/canvas/templates/held_drafts/…）
│   │   │   ├── db\models.py        ← 全部 ORM（单文件）
│   │   │   ├── ledger\             ← 通用幂等账本（record/lookup/replay，M6 落地事务的地基）
│   │   │   ├── canvas\ tasks\ guard\ reminders\ templates\ activity\ messages\ ← 领域模块
│   │   │   ├── orchestration\      ← M6b 主战场（现为空占位包）
│   │   │   ├── computers\hub.py    ← daemon 网关（连接/投递/唤醒/对账/后台扫描，~千行核心）
│   │   │   └── ws\ events\         ← 浏览器 WS 广播
│   │   └── migrations\versions\    ← Alembic 0001–0007（M6 建 0008/0009）
│   ├── apps\daemon\                ← daemon（适配器 adapters/claude_code.py + codex.py、探测 probe.py、MCP 工具 mcp.py）
│   ├── apps\web\                   ← React 前端（screens/components/lib；lib 内有与 py 内核镜像的 graph.ts/cron.ts 等）
│   ├── apps\mock-server\           ← 形状 mock（显式开启才用；**形状源非逻辑源**）
│   ├── packages\contracts\         ← Python 契约包（Pydantic 模型/枚举/常量/kernel 确定性内核）
│   ├── packages\contracts-ts\      ← TS 镜像（generated/ 由 pnpm gen 生成，勿手改）
│   ├── packages\fixtures\golden\   ← 双跑判例（graph.json/fingerprint.json；M6 加 decomposition.json）
│   └── docs\project-handoffs\      ← 交接文档体系（见 §4）；docs\verify\ = 实机证据归档
├── engineering_docs\               ← ★ 六契约（在 git 仓库外！）
├── docx_agenthub\CoAgentia-PRD.md  ← 产品需求文档
└── orchestrator_docs\              ← Orchestrator 拆解设计（M6b 实现级权威）
```

**三主体运行时**：浏览器（React，WS 收事件 + REST 写）←→ server（FastAPI，一切判定/状态机/账本）←→ daemon（每台机器一个，驱动 Agent 进程、执行 git/命令）。

## 4. 文档体系与阅读顺序

| 顺序 | 文档 | 读什么 |
| --- | --- | --- |
| 1 | 本文 | 全貌与规矩 |
| 2 | [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) | 当前状态快照（能力面/基线/挂账清单/启动方式） |
| 3 | [M6-HANDOFF.md](M6-HANDOFF.md) | **你的任务书**：范围/两块竖切/J0–J12 模块分解与 DoD/16 裁决/22 条出口清单 |
| 4 | [M6-DEV-PLAN.md](M6-DEV-PLAN.md) | 执行计划：波次编排/进度表（**你要维护它**）/关键锚点防返工 |
| 5 | `engineering_docs\` 六契约 | 按模块精读：A=表结构 B=REST+行为条文（M6 看 §12）C=WS 事件 D=daemon 帧 E/E2=适配器。**实现=契约填空，未列出的表/字段/帧/错误码不要发明** |
| 6 | `orchestrator_docs\Orchestrator任务拆解设计.md` | M6b 全生命周期权威（状态机/schema/V1–V14/修复循环/落地幂等/delta/失败全表/A1–A8 验收） |
| 7 | PRD / [PROJECT-RECORD.md](PROJECT-RECORD.md) | 需求背景 / 全部历史（含各里程碑 code-review 教训） |

## 5. 当前状态（2026-07-11）

- **M1–M5 全收口** = PRD M5 出口达成；**M6 已立项待开工**（契约修订已落笔：A v1.0.7 / B v1.4 / D v1.0.3；C/E/E2 零修订）。
- git：`main` @ `562af66`，工作树干净，**无 remote**。
- 测试基线（守门起点，**只增不减**）：后端 `uv run pytest -q` = **712 passed / 4 skipped**；前端 vitest = **175**；pyright **0 错**（并入 `pnpm typecheck`）；ruff 干净；`pnpm gen` 后 diff 为空；双侧 build 绿。
- owner 已拍板的 M6 方向（**已拍板勿再问**）：①交付链先行（M6a=FR-10 → M6b=FR-9）②worktree 工作目录=消息注入（适配器零修订）③挂账三件全收（评审结论枚举/模板 PATCH·DELETE/fail-closed 复核）④合并=`--no-ff`。

## 6. 工程纪律（历次里程碑沉淀，违反=返工）

1. **契约先行**：改行为先升契约版本+变更记录，再动代码；contracts 包 manifest 与契约文档双向同步（有对照测试守门）。
2. **生成物确定性**：`packages/contracts-ts/src/generated` 只经 `pnpm gen` 重生成，跑完 `git diff` 应为空；勿手改。
3. **一致性测试双跑**：新端点扩进 `apps/server/tests/test_conformance_dual.py`（真 server 与 mock 同形状）。
4. **mock 是形状源不是逻辑源**：业务逻辑只活在真 server/daemon；mock 只保证响应形状。
5. **进度记录义务**：每完成一个模块 → 更新 M6-DEV-PLAN 进度表（状态+提交哈希）+ CURRENT-HANDOFF；阶段结论沉淀 PROJECT-RECORD；**结论要截图实证**（实机证据归 `docs/verify/`）。
6. **owner 协作偏好**：全程中文；小瑕疵直接修不必问；大方向给选项问 owner；已拍板的不再问。
7. **判定语义单点**：值域/校验/判定只写一处（contracts 常量或服务层单点），前端不复制判定逻辑。
8. **确定性内核单源**：graph / fingerprint /（M6 新增）decomposition 三组内核 = py 权威 + ts 镜像 + golden 判例双跑，改任一语义三处同步。
9. **迁移纪律**：新迁移按批次显式点名建表（**勿 metadata.create_all 全集**——M1 坑）；给既有表加索引/约束须 `if_not_exists`；SQLite ADD COLUMN 安全可用。
10. **两个作用层**：通知/显示策略不动事实层（已读游标/投递）——凡"UI 面"配置严禁反向影响投递语义。
11. **gating 双面**（M3b 核心教训）：任何"扣住消息"的机制必须同时处理唤醒触发 **和** 投递批剔除+水位（`_filter_gated` 范式）。
12. **幂等三教训**（M4a/M5b code-review）：循环调度用**塌缩式重排**（勿逐格重放）；落地批 reserve-before + **可失败校验全部前置于 reserve**；`request_hash` 必须折入 source 身份；批内重放按 ledger `seq` 保序。
13. **daemon 是执行器**：DAG 序、冲突处置、gating、触发判定全在 server；daemon 只执行命令与上报。

## 7. 环境与命令

- **平台**：Windows 11 + Git Bash（POSIX sh）；Python 经 `uv`（仓库根 `uv run …`）；Node 经 `pnpm`。
- **启动开发**：终端 1 `uv run coagentia-server`（8787）；终端 2 `pnpm --filter @coagentia/web dev`（5173，代理 /api→8787）。
- **守门命令**（每波收口全绿才算过门）：

```bash
cd /d/Project4work/Agenthub_7_8/coagentia
uv run pytest -q                    # 712+/4 skipped 起点
pnpm -F @coagentia/web test         # 175 起点
pnpm typecheck                      # pyright 0 + 双 tsc
uv run ruff check .
pnpm gen                            # 之后 git diff 须为空
pnpm -F @coagentia/web build
```

- **实机 verify 范式**：临时库 `COAGENTIA_ALEMBIC_URL` 指到 scratch 文件 → alembic head → seed → 独立端口（8799/8801 先例）起 uvicorn → 探针脚本/playwright 截图；证据写 `docs/verify/M6*-EVIDENCE.md`。**结束必须杀掉起的进程**。
- **M6 实机的 git 靶子**：脚本生成 scratch git 仓库（临时目录 init+种子提交），**勿拿 coagentia 仓库本身当靶子**。

## 8. 已知坑清单（真实踩过，勿复踩）

| 坑 | 结论 |
| --- | --- |
| win32 子进程 | 杀进程用 `taskkill /F /T`（terminate 杀不干净进程树）；子进程 stdout **必须显式 utf-8 decode**（默认 gbk 会崩）——git 输出同样适用 |
| win32 git | 路径给正斜杠或原生反斜杠都可，但**锁文件/占用**会让 `worktree remove` 失败——M6 开工第一件事就是实测校准（J3-cal），结论写 `scratchpad/GIT-CALIBRATION.md` |
| 真 claude CLI | stream-json 必须带 `--verbose`；必须持续排空 stderr（否则管道塞死） |
| 真 codex CLI | `codex app-server` JSON-RPC 长驻；win32 用 codex.cmd；协议真值在 `scratchpad/CODEX-CALIBRATION.md`（若已清理，重跑 `codex app-server generate-json-schema`） |
| SQLite | 须 ≥3.35（RETURNING）；FTS 键于 rowid（VACUUM 会失同步——已知观察项勿"顺手修"） |
| Alembic | 见纪律 9；从零 `upgrade head` 与增量升级**双路都要测** |
| pyright | 基线 0 错并入 typecheck，新增类型债即红——用 `models.tbl`/`row_dict` 等既有窄化工具 |
| 端口残留 | 实机起的 uvicorn/daemon-sim/浏览器进程收尾必须关（8787/8799/8801） |

## 9. 交付文化（什么叫"完成"）

一个模块完成 = 任务书 DoD 达成 + 守门命令全绿 + 进度表更新；一个块收口 = 出口清单（M6-HANDOFF §9）逐条勾销 + **实机 verify 带截图证据** + `/code-review` 级别的自查（历史上每块都做 8 角度 review，CONFIRMED 项全修）；**测试基线只增不减**，实现与测试同批交付（先写失败测试再实现是常态）。宁可范围内做穿，不要范围外发明——挂账清单里的已知问题除非任务书点名，否则**不要顺手修**（防 diff 污染）。
