# M1 技术契约 —— 下一步任务书(HANDOFF)

> 建立:2026-07-08,设计线全 7 批收官之后。
> 用途:工程线开工的唯一入口。**设计线已完成(见 [HANDOFF.md](HANDOFF.md));本文件描述下一步:写 M1 技术契约,让前后端并行。**
> 状态:**✅ 已完成**(2026-07-09):五契约冻结 + contracts 包 + mock + P1 形状验证,交付见 [SESSION-HANDOFF.md](SESSION-HANDOFF.md) §2。本文件仅存档。**后继任务书 = [M1-IMPL-HANDOFF.md](M1-IMPL-HANDOFF.md)(M1 实现阶段)。**

---

## 0. 一句话目标

把 **M1 基座**的所有跨模块契约一次性冻结成**同源、可编译、前后端共享**的定义,使得:

- **前端**照设计稿(afterglow-ds 15 屏)+ 这套契约的 **mock 数据**独立开发,不等后端;
- **后端 / daemon**照同一套契约实现,不等前端;
- 双方在契约上对齐,集成时只接线、不返工。

这一步**只产出契约与 mock,不产出业务实现**。

---

## 1. M1 里程碑范围与出口(PRD §8,勿扩界)

| 项 | 内容 |
| --- | --- |
| **M1 范围** | 工作区 / 频道 / 线程 / DM / 消息不可变 / @解析;daemon + **Claude Code 适配器**;Agent CRUD + 生命周期 + Home + 三档重置;**WebSocket 实时** |
| **M1 出口标准** | 两个 Agent 在频道里完成一次真实对话与文件产出 |
| **本任务(契约)出口** | 下列 A–E 五份契约冻结 + `packages/contracts` 骨架落地 + 一份 mock server/fixtures 能驱动设计稿渲染真实形状数据 |

> M6(Orchestrator / 拆解 / 画布引擎)**不在 M1**,但 M1 契约**必须为它预留**接口(见 §4)——这是本任务的核心价值点。

---

## 2. 交付物(5 份契约 + 1 个包)

> 事实源实体/权限/决策见 §5。每份契约建议用 **TypeScript 类型 + JSON Schema/zod**(同构,前后端共享),而非散在文档里。

### A. 实体表 / 数据模型
- 覆盖 PRD §2.2 全部实体:Workspace / Computer / daemon / Agent / Runtime适配器 / AgentHome / Channel·Thread·DM / Message / Task / Contract(TaskPlan·TaskHandoff·LoopContract)/ Canvas / HeldDraft / DiagnosticEvent / Reminder / Template / Orchestrator / Project / DecompositionProposal / Worktree / PreviewSession / Deployment。
- **硬约束**:核心实体带 `workspace_id`(预留多租户,todo);频道预留跨工作区引用维度(FR-1.5);消息**不可编辑不可删除**;Task 频道内自增编号 + 5 态 + 同刻唯一 owner;Message 的 `@` 与 `task #n` 是**纯文本服务端解析**,不是外键。
- **必含账本表**(见 §4 预留#1):opId `unique`、`requestHash` 列、`batch_id` 维度、fail-closed 告警路径。
- **画布实体**带基线版本号 + 快照指纹字段(见 §4 预留#2)。

### B. REST API 契约
- 资源型接口:工作区/频道/成员/Agent CRUD、消息发送、任务 CRUD 与状态写、Home 文件浏览、Project 绑定/配置、三档重置触发等。
- 权限矩阵按 PRD §3.1/§3.2 编码(注意 **R4 任务状态写全员开放**、**R8 部署触发全员含 Agent**、**R1 Agent 永不 Owner**)。
- **确认类接口带 CAS 语义**(见 §4 预留 S2):草稿/delta 确认携带客户端渲染指纹,不匹配 → `409 + 最新态`。

### C. WebSocket 事件协议
- NFR1:Agent 状态点 / 任务牌 / 画布节点色 / 未读角标**全部 WS 实时推送,无手动刷新**。定义事件信封(类型、workspace/channel 作用域、序号、幂等键)。
- **预留草稿层事件族**(见 §4 预留#4):`draft.presented/adjusted/confirmed/superseded`、`delta.*`——**事件名直接沿用拆解设计 §15 的 DiagnosticEvent 类型,一名两用**。
- 覆盖消息/任务/presence/画布/护栏(HeldDraft)/部署日志流等事件。

### D. daemon ↔ server 协议
- daemon 五职责的线协议:保连接 / 跑 Agent / 管进程(start·stop·sleep·wake)/ 投递消息 / 跑交付进程(预览 dev server·合并·部署)。
- 接入:`coagentia-daemon --server-url <url> --api-key <key>`(**api-key 命令行明文,owner 决策**);断连则其上 Agent 全 Offline,重连自动 resume。
- **落地联动定义为幂等事件消费,不是 RPC**(见 §4 预留#5、S4):激活唤醒/worktree 派生按任务幂等、at-least-once;**服务端批次 `:done` 是唯一事实源**。
- **消息直投变体**(S1):系统消息定向投递给单个 Agent、不进频道流、写 DiagnosticEvent。

### E. Claude Code stream-json 适配器进程模型
- 命令:`claude --output-format stream-json --input-format stream-json --include-partial-messages --permission-mode bypassPermissions`(**bypassPermissions 为 owner 决策**)。
- 定义:进程生命周期(start/stop/sleep/wake 对应)、stdin/stdout 的 stream-json 消息帧、partial-message 增量 → WS 事件的映射、崩溃自动拉起、Agent 身份≠会话(重启后 Home/记忆/成员关系保留)。
- Codex(`codex app-server --listen stdio://` 长驻)是 **M5**,M1 适配器接口需可扩展但不实现。

### F. `packages/contracts`(monorepo ③层)
- 03 §3 的三层架构:**成员数据 + 服务端领域模块 + 同构纯内核包**;`packages/contracts` 是同构纯内核包,前端与服务端**同源引用**同一套类型/schema/事件名/opId 前缀。
- 扩展走**数据与 schema**(schema 版本号、角色模板记录、opId 前缀),**不装插件**(03 §5)。

---

## 3. 让前后端并行的关键:mock

- 契约冻结后,产出一份 **mock server(或 MSW/fixtures)**,按 A–C 契约吐真实形状的样例数据(样例数据用设计稿同款:Memcyo/Pat/Hank/Rin/Orchestrator、#build「番茄钟 MVP」、任务 #1–#7、指纹 a1b2c3)。
- 目标:把 afterglow-ds 的静态屏接上 mock,变成能点、能收 WS 事件刷新的**活原型**;后端就绪后切真实端点。

---

## 4. 必须落进 M1 的预留清单(03 §6 + §4,**漏一条 M6 就是破坏性变更**)

| # | 预留 | 出处 |
| --- | --- | --- |
| 1 | **账本表**进实体清单:opId `unique`、`requestHash`、`batch_id` 维度、fail-closed 告警路径 | S5 / 01 §5.1 |
| 2 | **画布实体**带基线版本号 + 快照指纹字段 | S3 |
| 3 | **消息投递协议**含"定向直投、不进频道流"变体 | S1 |
| 4 | **WS 事件**预留草稿层事件族(draft.*/delta.*),事件名沿用设计 §15 DiagnosticEvent 类型 | 预留#4 |
| 5 | **daemon↔server** 落地联动 = 幂等事件消费,非 RPC;批次 `:done` 唯一事实源 | S4 |
| 6 | Monorepo 预留 `packages/contracts`,前后端同源引用 | §3 |

**关键接缝(S1–S5)**是跨模块协议,M1 契约要显式定义:S1 直投变体 / S2 确认 CAS(带指纹,409 回最新态)/ S3 画布基线快照(单调版本号 + 指纹,人类编辑与 delta 校验同一串行化点)/ S4 daemon 幂等处理器 / S5 账本表约定。

---

## 5. 事实源(开工先读这些,勿凭空发挥)

| 读什么 | 取什么 |
| --- | --- |
| [CoAgentia-PRD.md](../../../../docx_agenthub/CoAgentia-PRD.md) v1.4 | §2.1/§2.2 实体、§3 权限矩阵与人/Agent 边界(R1–R8)、§8 里程碑、FR-1/FR-2、NFR1/NFR2/NFR8、§9 非目标 |
| [orchestrator_docs/03-接入架构建议.md](../../../../orchestrator_docs/03-接入架构建议.md) | **§4 接缝 S1–S5、§6 M1 预留清单(本任务直接输入)**、§3 三层架构 |
| [orchestrator_docs/01-可行性评估报告.md](../../../../orchestrator_docs/01-可行性评估报告.md) §5 | 拆解设计 **2 处待修缺陷**——M1 阶段**只需按 §6 预留,不必现在修**(修在 M6) |
| [orchestrator_docs/Orchestrator任务拆解设计.md](../../../../orchestrator_docs/Orchestrator任务拆解设计.md) | §15 DiagnosticEvent 类型清单(WS 事件名一名两用的来源)、schema/opId 命名 |
| [HANDOFF.md](HANDOFF.md) 关键决策速查 | 已拍板、勿再问的约束 |
| `docx_coagentia/04-设计稿/afterglow-ds/` | 契约要能驱动的 15 屏 UI(反推所需字段/事件) |

---

## 6. 已拍板的决策约束(编码进契约时遵守,勿再问)

- **单人 MVP**:唯一人类 = Owner,本地 Web UI 无登录页;数据模型**保留多人 Member 结构**(FR-1.2、非目标 #11)。
- **任务状态写权限全员开放**(R4,不做角色校验;done 须人批准靠简报约定,不靠 schema)。
- **部署触发全员含 Agent**(R8),全量留痕进账本。
- **bypassPermissions + api-key 命令行明文** = owner 刻意决策。
- **DM 不承载任务**(FR-5.1)。**深链** `?tab=canvas&node=`。**delta 部分接受**语义见拆解设计 §11。
- **消息不可变**;`@`/`task #n` 纯文本服务端解析。

---

## 7. 建议推进顺序(供参考,非硬约束)

1. **实体表 + 账本表 + 画布快照字段**先行(A,含预留 #1/#2)——一切的地基。
2. **WS 事件信封 + 事件目录**(C,含预留 #3/#4)——NFR1 是产品灵魂,早定。
3. **REST 契约 + CAS 接口**(B,含 S2)。
4. **daemon↔server 幂等协议 + Claude Code 适配器进程模型**(D/E,含 S1/S4/S5)。
5. `packages/contracts` 落盘 + **mock**,接一屏设计稿验证形状(建议先接 P1 会话或 P3 看板)。

每步产出即评审:契约能否驱动对应设计屏的所有交互?能否表达对应 FR?

---

## 8. 需 owner 拍板的开放问题(开工前建议先问)

> **2026-07-09 更新:四个问题已全部由 owner 拍板,本节仅存档。技术选型定稿于 [engineering_docs/00-技术选型.md](../../../../engineering_docs/00-技术选型.md) v1.1(同日两轮:先定 Node 全栈,后 owner 改判**后端与 daemon 改 Python**)。**

1. ~~**技术栈**~~ ✅ 已定(v1.1):**后端 Python**(FastAPI + SQLite/SQLAlchemy 2.0+Alembic + Starlette WS + uv workspace)+ **前端 TS**(React 19+Vite、React Flow、TanStack Router/Query、vanilla CSS+tokens)+ **daemon Python 同栈**(`uvx coagentia-daemon`),见 00-技术选型.md §2 决策总表。
2. ~~**契约表达形式**~~ ✅ 已定(v1.1):**Pydantic-first**——`packages/contracts` 的 Pydantic v2 模型为唯一源,经 OpenAPI/JSON Schema 单向生成 TS 侧;**03 §3.3 同构承诺降级为"同一 schema 源 + 双实现 + 跨语言金标向量"**(owner 知悉接受);Orchestrator 提案校验器按 V5 为手写安全子集,归 contracts/kernel(Python 权威 + TS 镜像)。本文件 §2 各契约建议中的"TypeScript 类型 + JSON Schema/zod"字样以此为准替换。
3. ~~**旧 React 组件库**~~ ✅ 已定:不做基座、仅作参考,从 afterglow-ds 静态屏重搭(00-技术选型.md §4.1)。
4. ~~**M1 是否连带框架化静态屏**~~ ✅ 已定(owner 拍板 2026-07-09):**契约+mock 先行**——本任务只交付 5 份契约 + contracts 包 + mock server,mock 仅接 1 屏(P1 或 P3)做形状验证;15 屏 React 重搭为独立工作流,随 M1 实现阶段启动、消费现成 mock。

---

## 附:与设计线的关系

设计线(afterglow-ds,15 屏 + 组件卡,7 批全完)是本契约的**需求实证**:每个字段/事件都应能在某张设计屏上找到落点。契约冻结后,设计屏 + mock = 可交互原型,是 M1 出口"两个 Agent 完成真实对话与产出"的前端载体。
> 归档说明：本文是 M1 契约阶段的已完成任务书，内容不再更新。阶段结论见 [项目记录](../PROJECT-RECORD.md)，当前状态见 [当前交接](../CURRENT-HANDOFF.md)。
