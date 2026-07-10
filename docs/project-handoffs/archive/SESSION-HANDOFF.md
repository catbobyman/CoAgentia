# 会话交接（SESSION-HANDOFF）——工程线 M1 契约进行中

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-09，契约 A/B/C 冻结 + 品牌改名 CoAgentia 之后 |
| 用途 | **下一个会话的入口文件**：当前状态、该读什么、注意什么、下一步任务书 |
| 与其他交接文件的关系 | [HANDOFF.md](HANDOFF.md) = 全局权威（设计线全量内容 + 品牌改名记录在那里）；[M1-HANDOFF.md](M1-HANDOFF.md) = 契约任务书（**已完成归档**）；**[M1-IMPL-HANDOFF.md](M1-IMPL-HANDOFF.md) = 当前任务书（M1 实现阶段，下一步从它开工）**；本文件是最新会话快照，冲突时以本文件为准 |

---

## 1. 项目一句话与当前阶段

**CoAgentia**（2026-07-09 由 AgentHub 改名）：契约驱动、流程可编排、护栏可干预的多 Agent 协作平台。

> **✅ M1 实现阶段收口（2026-07-09）**：代码仓 `coagentia/` 分支 `m1-impl`（11 commits）落地 A1–A8 后端（server 17 表/账本/REST 39 端点/浏览器 WS/daemon 网关+对账器/daemon 本体/Claude Code 适配器/端到端集成）+ B1–B2a 前端（TanStack 基座 + 批1+2 七屏）。**230 pytest + 2 skipped(真CLI冒烟) + 双侧 typecheck 全绿**；PRD M1 出口 12 条全达成（真机：两 claude-haiku Agent 频道对话 + 文件产出 + reminder + B4 同源零改动）。`/code-review high` 揪出 10 条 CONFIRMED bug + live 复验 1 条，**全修 + 回归测试**。执行计划与实证见 [M1-DEV-PLAN.md](../../../../M1-DEV-PLAN.md)（§0 收口横幅）。**下一步**：M2（任务与看板）或先合 m1-impl→main + 补 B2b/B3。当前 main 仍是契约收口态；实现全在 m1-impl 分支未合并。

当前设计线已收官归档（15 屏 + 组件卡，7 批全完）；工程线 **M1 五份契约 A–E 冻结 v1.0** 且**实现已完成**（上）。

## 2. 当前状态速览

| 事项 | 状态 |
| --- | --- |
| [00-技术选型.md](../../../../engineering_docs/00-技术选型.md) | **v1.1 定稿**：后端+daemon = Python（FastAPI / SQLite+SQLAlchemy 2.0+Alembic / Starlette WS / uv）；前端 = TS（React 19+Vite / React Flow / TanStack）；契约 = Pydantic-first 单向生成 TS。注意 v1.0（Node 全栈）当天被 owner 改判，勿翻旧账 |
| [01-实体表与数据模型.md](../../../../engineering_docs/01-实体表与数据模型.md)（契约 A） | **v1.0.1 冻结**：34 表（v1.0.1 修正计数）、指纹序列化规范（§2）、账本 batch_id 修订采纳（§4.7）、画布基线语义（§6）、分里程碑建表节奏（§5） |
| [02-REST-API契约.md](../../../../engineering_docs/02-REST-API契约.md)（契约 B） | **v1.0.1 冻结**：三主体身份模型（§2）、20 错误码（§3，v1.0.1 修正计数）、13 域端点（§4）、S2 确认 CAS（§5）、幂等重试复用账本（§1）；§6 read-position 附带落地为 `ChannelsSnapshot` |
| [03-WS事件协议.md](../../../../engineering_docs/03-WS事件协议.md)（契约 C） | **v1.0 冻结**：信封四要素（§3）、30+ 事件目录（§6）、断线恢复 = REST 重同步无 outbox（§4）、draft/delta 一名两用（§7） |
| [04-daemon-server协议.md](../../../../engineering_docs/04-daemon-server协议.md)（契约 D） | **v1.0 冻结**（2026-07-09）：五 kind 帧模型、握手对账（无指令 outbox=DB 事实源推导补发，与 C 无 outbox 同构）、指令/查询/上报三目录（S1 直投·S4 幂等消费·M6/M7 预留）、投递语义（deliver ack→写 read_positions=Agent 已读游标）、数据目录 `~/.coagentia/`（裁决 A §10.2；B §8.1 孤儿文件=staging+sidecar+24h GC，A/B 无需修订）；agents.status 列不被断连级联改写（resume 期望依据）；新挂开放问题：projects 缺 computer_id（M6 建表前补） |
| [05-ClaudeCode适配器进程模型.md](../../../../engineering_docs/05-ClaudeCode适配器进程模型.md)（契约 E） | **v1.0 冻结**（2026-07-09）：FR-2.4 命令行 + `CLAUDE_CONFIG_DIR` 配置隔离（R6 技能白名单物化）、coagentia MCP 行为通道（Agent 行为唯一出口，正文不外发）、session 簿记于 `daemon/state/`（补充 D §9.1）、三档重置映射（reset_full 清 Home 即清会话）、崩溃拉起 1s/5s/15s + 5 分钟窗 3 次熔断、activity 相位聚合（**裁决 C §10.1：不新增工具级事件**，细粒度走 diagnostic.appended 订阅流）、usage=result 帧提取 + ULID 去重 + thread_root_id 提示由 server 富化 task_id、RuntimeAdapter 接口 + Codex（M5）扩展位 |
| `packages/contracts` + mock + P1 验证 | **✅ 完成（2026-07-09），M1 契约任务收口**。代码仓 = 项目根 [coagentia/](../../../README.md)（独立 git，owner 拍板选址）：contracts 包（Pydantic 唯一源 + **manifest 对照测试**把五份契约的 34 表/20 错误码/56 事件/29 帧逐名钉死）· kernel 指纹（A §2 实现 + golden 判例 10 条）· fixtures（设计稿同款 84 行 + WS 时间线 5 事件，生成脚本确定性重跑）· mock server（REST M1 端点 39 条 + WS 信封 + `/__mock/play` 回放）· TS 生成管线（`pnpm gen` 重跑 diff 为空守门）· **P1 会话屏**（apps/web，零手写实体类型，owner 拍板选 P1）。**38 pytest + 双侧 typecheck 全绿；playwright 截图实证在 coagentia/docs/verify/**（静载 + 回放前后三张：任务牌移列、token 徽章、presence 变点、未读线全部无刷新更新——NFR1 首次实证） |
| 品牌改名 | 文档层**已完成**（含 schema 版本号 `coagentia.*`、`coagentia-daemon` 等技术标识）；**两个目录改名待收尾**：用户关闭会话后运行 `D:\Project4work\finish-rename-coagentia.cmd`（docx_agenthub→docx_coagentia、Agenthub_7_8→CoAgentia_7_8）。记忆已预迁移到新项目 key |
| 设计线 | 收官归档；**设计资产仍是 AgentHub 品牌**（logo 像素 A、wordmark、远端项目名）——设计品牌改名是独立任务，待 owner 发起 |

## 3. 恢复上下文：按需读什么

| 场景 | 读什么（按序） |
| --- | --- |
| 任何会话开场 | 本文件 → [engineering_docs/README.md](../../../../engineering_docs/README.md)（文档清单与状态）；需要设计线/全局背景再读 [HANDOFF.md](HANDOFF.md) |
| **写契约 D（下一步）** | [M1-HANDOFF.md](M1-HANDOFF.md) §2D · [02-实现难点分析.md](../../../../orchestrator_docs/02-实现难点分析.md) §1.3(c)（saga/幂等消费的论证）· [03-接入架构建议.md](../../../../orchestrator_docs/03-接入架构建议.md) §4 接缝 S1/S4 · 契约 A §4.7（账本/批次）与 §10（挂给 D 的开放问题）· 契约 B §2（daemon 身份）/§4.13（占位）/§8.1（孤儿文件挂给 D）· 契约 C 头表（daemon WS 与浏览器 WS 的边界声明）· 00 选型 §4.5（uvx 分发、asyncio 子进程）· PRD FR-2 · [体验全流程.md](../../../../docx/hands-on/体验全流程.md)「磁盘侧证据」节（Raft daemon 的实锤参照） |
| 写契约 E | M1-HANDOFF §2E · PRD FR-2.4/2.5、§4.4 生命周期 · 契约 C §6.2（agent.activity/presence 事件）与 §10.1（帧密度问题挂给 E）· 体验全流程「磁盘侧证据」（claude.exe 完整命令行实锤） |
| 写 contracts 包 + mock | M1-HANDOFF §3 · 00 选型 §3（包布局）· 示例数据规范 = [CLAUDE-DESIGN-CONTEXT.md](../../../../docx_agenthub/04-设计稿/afterglow-ds/CLAUDE-DESIGN-CONTEXT.md)「示例数据」节（Memcyo/Pat/Hank/Rin、#build 番茄钟、任务 #1–#7、指纹 a1b2c3——全项目统一，勿换名字） |
| 产品语义拿不准 | [CoAgentia-PRD.md](../../../../docx_agenthub/CoAgentia-PRD.md)（注意：目录改名前实体路径是 `docx_agenthub/`）· 交互细节查 [交互说明.md](../../../../docx_agenthub/03-设计文档/交互说明.md) |

## 4. 已裁决勿重开（本轮会话新增决策，全部有溯源）

1. **技术栈**：Python 后端/daemon + TS 前端 + Pydantic-first（00 §2）；03 §3.3 同构承诺**降级**为"同一 schema 源 + 双实现 + 金标向量"（owner 知悉接受，00 §4.4）。
2. **M1 范围**：契约+mock 先行；15 屏 React 重搭是独立工作流（M1-HANDOFF §8.4）。
3. **断线恢复不建 outbox**：WS=通知面 / REST=事实源，重连即 REST 重同步（契约 C §4，同时裁决了契约 A §10.1）。
4. **幂等重试复用账本**：`Idempotency-Key` → `op_id=rest:<key>`（契约 B §1）。
5. **Agent 发消息与护栏同端点**：freshness 命中 → `202 + HeldDraft`（契约 B §4.6）。
6. **草稿调整客户端累积**，confirm 一次性提交并全量重验，不设逐笔调整端点（契约 B §4.10）。
7. force-start 仅人类（C3）；部署冲突 409 不排队；DM 与频道合表（`channels.kind`）；成员名工作区唯一（NOCASE，@解析推导）；卡片 = 不可变锚点消息 + 实体状态走 WS；画布基线快照**不含坐标**；指纹禁 float、SHA-256 短码 6 位。
8. 幂等键 batch_id 修订（可行性报告 §5.1 必修项）已采纳进契约 A §4.7——M6 不用再修。

## 5. 下一步任务书

> **2026-07-09 更新：§5.1/§5.2/§5.3 全部完成**（覆盖情况见 §2 状态表），以下各节仅存档任务书原文。**M1 契约任务已收口；当前下一步 = M1 实现阶段，任务书 = [M1-IMPL-HANDOFF.md](M1-IMPL-HANDOFF.md)**（两线并行分解 A1–A8 / B1–B4、每模块 DoD、纪律六条、开放问题挂账总表、出口验收清单、环境坑备忘——开工直接读它）。**执行计划已写就 = [M1-DEV-PLAN.md](../../../../M1-DEV-PLAN.md)**（2026-07-09；模块内步骤、阶段/会话切分 S1–S7/T1–T5、测试五层、验收映射、风险八条；进度状态记在其 §1 总表，开发会话按它推进）。

### 5.1 契约 D：daemon ↔ server 协议（`engineering_docs/04-daemon-server协议.md`）——✅ 已完成

必须覆盖（对应 M1-HANDOFF §2D + 预留 #3/#5）：

1. **接入与认证**：`uvx coagentia-daemon --server-url <url> --api-key <key>`（明文 = owner 决策）；REST Bearer + `/api/daemon/ws` 握手；心跳；断连 → 其上 Agent 全 Offline 级联（契约 C 已定 `computer.disconnected` 级联事件）；重连自动 resume。
2. **五职责线协议帧**：保连接 / 跑 Agent（start·stop·sleep·wake 指令帧）/ 管进程 / 投递消息 / 交付进程（dev server·合并·部署命令执行）。
3. **S1 消息直投变体**：定向投递单个 Agent、不进频道流、写 DiagnosticEvent（修复循环与"请起草契约"都靠它）。
4. **S4 幂等事件消费**：server→daemon 指令 at-least-once + ack；daemon 处理器按任务幂等（唤醒/worktree 派生）；**服务端批次 `:done` 是唯一事实源**；"daemon 离线时落地、恢复后补触发"要写成明确用例。
5. **遥测上行**：diagnostic 批量、token usage、presence/busy_detail、detected_runtimes——形状引用契约 A 表。
6. **数据目录布局**：收掉两个挂账的开放问题——契约 A §10.2（DB 文件/files 存储/部署日志落点，如 `~/.coagentia/`）与契约 B §8.1（孤儿文件 GC）。
7. **与契约 E 的边界**：D = 线协议与职责分工；E = Claude Code 进程模型与 stream-json 帧映射。

### 5.2 契约 E：Claude Code 适配器（`engineering_docs/05-ClaudeCode适配器进程模型.md`）——✅ 已完成

命令行（PRD FR-2.4，bypassPermissions = owner 决策）、进程生命周期 ↔ Agent 状态机映射、stream-json 帧 → WS 事件映射（partial → `agent.activity`，需顺手裁决契约 C §10.1 的帧密度问题）、崩溃自动拉起、身份≠会话、usage 帧 → token_usage_events、Codex（M5）扩展位。

### 5.3 然后——✅ 已完成

`packages/contracts` 骨架 + mock server（FastAPI 薄应用 + fixtures）→ 接 **P1 会话或 P3 看板**一屏验证形状 → M1 契约任务收口。每完成一份：更新 [engineering_docs/README.md](../../../../engineering_docs/README.md) 状态行 + HANDOFF.md 工程线行（惯例已立）。

## 6. 注意事项（坑与纪律）

1. **改名中间态**：收尾脚本跑之前，文档里的 `docx_coagentia/` 链接指不到实体目录（实体仍叫 `docx_agenthub/`）——这是已知状态，**勿"修复"回旧名**；若发现目录已是 `docx_coagentia` 说明脚本已执行，可删除本条。
2. **远端设计项目名保留原文**："Afterglow — AgentHub Design System"（`f8708be9-…`）与 "AgentHub Terminal Bauhaus"（`0b6d2bba-…`）是 claude.ai/design 实名，引用勿改成 CoAgentia；后者已定**不做前端基座**、远端勿动。
3. **afterglow-ds/ 镜像纪律**：本地 = 远端逐字节镜像，里面全部文件（含 HTML/build.py/两个 md）都还是 AgentHub 品牌——**刻意未动**，任何改动必须走 HANDOFF.md 的 verify SOP 推回远端。
4. **文档纪律**（已立惯例）：端点/事件/表/错误码新增必须先登记进对应契约目录；修订升版本号 + 变更记录；M2+ 的表在迁移落地前允许修订（契约 A §5 冻结策略）。
5. **仍挂着的开放问题**（勿当成漏项重新发明）：A §10.3 api-key 轮换（后置）、A §10.4 FTS 中文分词（M2 实测）、B §8.3 Agent 频控（后置）、C §10.1 activity 粒度（**契约 E 顺手裁决**）、C §10.2 多人事件过滤（后置）；A §10.2 与 B §8.1 由契约 D 收掉。
6. **Owner 协作偏好**：中文；微瑕直接修、大事给选项问（用选项式提问，推荐项放第一个）；结论要实证；已拍板的决策勿再问（PRD 附录 B + 00 附录溯源表 + 本文件 §4）。
7. **代码仓纪律**（2026-07-09 起）：`coagentia/` 是项目根下独立 git 仓；生成物三类（fixtures/seed·timeline、golden 判例、contracts-ts/src/generated）**只能经脚本重生成**（`gen_fixtures.py` / `gen_golden_fingerprint.py` / `pnpm gen`），重跑 diff 必须为空；fixtures 中任务 **#6「冒烟测试脚本」是补位项**（设计稿样例跳过 #6，为守「编号频道内自增无空洞」不变量而增补），其余名字/编号一律设计稿原样。
8. **两个统计笔误已修**（v1.0.1，contracts manifest 测试核出）：契约 A 表数 31→34、契约 B 错误码 19→20——历史文档（HANDOFF 旧行、M1-HANDOFF）里的旧数字不再回溯，以契约文档现行版为准。

## 7. 本会话产出清单（备查）

**2026-07-09 第二会话新增**：`engineering_docs/04-daemon-server协议.md`（契约 D v1.0）· `05-ClaudeCode适配器进程模型.md`（契约 E v1.0）· **代码仓 `coagentia/`**（monorepo：contracts 包 + 对照测试 38 条 + fixtures + mock server + TS 生成管线 + P1 验证屏 + verify 截图 ×3，git 历史 6 commits）· 契约 A/B 升 v1.0.1（计数修正）· 本文件与 HANDOFF/engineering_docs README 状态同步。

**首会话产出**——新建：`engineering_docs/`（README + 00/01/02/03 四份文档）· `SESSION-HANDOFF.md`（本文件）· `D:\Project4work\finish-rename-coagentia.cmd`。
修改：HANDOFF.md（品牌改名节 + 工程线状态行）、M1-HANDOFF.md（§8 全部关闭）、全仓 .md 的 CoAgentia 改名（含 `docx/` 竞品研究中的本项目提及；PRD 改名为 CoAgentia-PRD.md）。
记忆：`coagentia-afterglow-design-line.md`（更新并改名）+ MEMORY.md 索引，已同步复制到新项目 key `D--Project4work-CoAgentia-7-8`。
> 归档说明：本文是 M1 首次收口时的会话快照，已被 hardening 后的交接取代。阶段结论见 [项目记录](../PROJECT-RECORD.md)，当前状态见 [当前交接](../CURRENT-HANDOFF.md)。
