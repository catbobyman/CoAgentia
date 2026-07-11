# M5 模板与第二 runtime —— 任务书（M5-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-11，M4 收口（`1052ee6`）后立项；契约修订核对与落笔已随本次立项**先行完成**（§1）；owner 四项方向裁决已拍板（§7 #1–#4） |
| 用途 | **M5 里程碑的唯一任务书入口**：把模板（工程三角/存为模板/向导实例化）与第二 runtime（Codex 适配器/技能白名单 UI/每频道通知设置/cron cadence）装进系统。前置任务书 [M4-HANDOFF.md](archive/M4-HANDOFF.md) 已完成归档 |
| 上游事实源 | [engineering_docs/](../../../engineering_docs/README.md) 六契约（**A v1.0.6 / B v1.3 / C v1.0 零修订核对 / D v1.0.2 / E v1.4 / E2 v1.0 新建**，M5 修订已全部落笔，见 §1）· [CoAgentia-PRD.md](../../../docx_agenthub/CoAgentia-PRD.md) FR-7 模板与向导 / FR-2.3/2.5 探测与 Codex / FR-3.6 技能白名单 / FR-4.7 通知 / §8 里程碑 · [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)（M4 收口态） |
| 执行计划 | 待建：开工首会话按本任务书建 `M5-DEV-PLAN.md`（体例同 [M4-DEV-PLAN.md](M4-DEV-PLAN.md)） |
| 出口标准 | **PRD M5 出口**：从模板一键实例化，**Claude 审 Codex 跑通一个交付**（owner 确认本机 Codex CLI 已装可用——真机验收，§9 逐条清单） |

---

## 0. 一句话目标

M4 给系统装上了护栏与提醒；M5 让系统**长出第二个大脑、流程可复制**：Codex Agent 与 Claude Agent 混跑同频道（护栏/任务/契约全语义对第二 runtime 生效），画布流程一键存为模板、向导三步实例化出整条工程三角流水线（评审与实现默认异 runtime 互审），技能白名单有了真 UI 与候选池，人类的通知面每频道可调（DM 恒必达），循环 Reminder 补上 cron——流程从"手抄"变成"资产"，runtime 从"单一"变成"互审"。

## 1. 契约修订摘要（**已全部落笔**，2026-07-11 随立项完成——纪律 1 的完成态而非待办）

| 契约 | 版本 | 本次修订内容 | 状态 |
| --- | --- | --- | --- |
| A 实体表 | v1.0.5 → **v1.0.6** | `templates.body` 由 JsonValue 收紧为 **`TemplateBody`** 嵌套模型（nodes/edges/roles/briefing + 保存序列化规则 + builtin 启动 upsert 裁决）；`computers.detected_runtimes` 扩 **`skills[]`** 候选技能池（列出≠授予，JSON 列扩字段零迁移）。核对确认：`reminders.cadence` v1.0 即预留 cron、`channel_notification_settings` 形状已冻结——**0007 批次 = templates + channel_notification_settings 两张表，无既有表加列** | ✅ 已落笔 |
| B REST | v1.2 → **v1.3** | 新 **§11 M5 规范条文**（模板保存序列化/实例化事务与 briefing/互审=UI 责任/技能池消费/通知 mode 消费规则/cron 值域与塌缩重排）；§4.12 模板三端点细化（**收窄移除内联 `{create:{...}}`**）+ §4.5 notification-setting 细化（人类本人自治）= **ENDPOINTS_M5 五端点**；`POST /reminders` cadence 扩 cron；错误码 +`NOTIF_IN_DM` +`TEMPLATE_CANVAS_NOT_READY`（23→25） | ✅ 已落笔 |
| C WS | — | **零修订**（连续核对）：模板/通知无实时事件面（B §11.2 #4 裁决——实例化广播走既有 task/canvas/message 事件；列表走 REST 拉取） | ✅ 核对完 |
| D daemon | v1.0.1 → **v1.0.2** | `runtimes.detected` 扩 `skills[]`（加字段向后兼容，无新帧类型）；§10 Codex 行落核对结论——**codex 上线零新增帧/指令/上报类型** | ✅ 已落笔 |
| E 适配器 | v1.3 → **v1.4** | Codex 实现规范移交 **契约 E2**（本篇保持 CC 专属、§9 接口原样）；探测扩技能池（只读列名不复制）；`create_reminder` cadence 扩 cron（**M5 工具组为空——连续第三个里程碑零新增 Agent 工具**） | ✅ 已落笔 |
| **E2 Codex**（新） | **v1.0 新建** | [06-Codex适配器进程模型.md](../../../engineering_docs/06-Codex适配器进程模型.md)：一 Agent 一进程裁决 / CODEX_HOME 隔离与 MCP 注入 / 会话簿记与三档重置（resume 降级语义）/ 长驻生命周期（握手即 idle）/ JSON-RPC 事件映射（相位聚合继承）/ **5 项开放问题 = H2 开工 A 级实测校准并回改文档**（帧名是外部依赖，冻结映射语义不冻结方法名） | ✅ 已落笔 |

> **纪律 1 中间态**：契约文档修订已先行完成；contracts 包 manifest 同步 = H0 模块（开工第一步之一），任何业务实现之前完成。

## 2. 范围与非目标（勿扩界）

**范围**（PRD §8 M5 行 + owner 拍板增补）：

- **Codex 适配器**（FR-2.5，契约 E2）：`codex app-server` 长驻进程驱动、按 `boot.runtime` 分派、probe_codex 探测（未装置灰 UI 已就位）；护栏/任务/契约/usage 对 codex Agent 全语义生效
- **技能白名单 UI**（FR-3.6）：P6 技能页签编辑态（后端 M1 已 100% 就绪零修订）；候选池 = daemon 探测本机技能目录上报（`detected_runtimes[].skills`，owner 拍板）
- **每频道通知设置**（FR-4.7，B §11.4）：GET/PUT 端点 + `mute` 掐 mention activity 生成门 + ChannelsSnapshot 扩字段 + 前端徽标/桌面通知按 mode（DM 恒必达 `NOTIF_IN_DM`）
- **cron cadence**（M4 裁决 9 顺延件收回，owner 拍板，B §11.5）：五段式解析单点 + 塌缩式重排（M4a 重放风暴教训的 cron 变体）
- **模板**（FR-6.6/FR-7，B §11.1/§11.2）：存为模板（画布快照序列化）/ 工作区级列表 / 向导三步实例化（单事务落地批 `tmpl:` 幂等，复用 M3b create_node 全链 + briefing 系统消息 @角色开工）/ **工程三角 builtin**（contracts 常量 + 启动 upsert）/ 跨 runtime 互审 warning（UI 责任）
- **P12 频道设置弹窗**（挂账 UI 一并收，§7 #13）：基本/通知/提醒阈值/护栏阈值四组（阈值管道 M1 全通纯 UI）
- **0007 建表批次**（A §5）：templates + channel_notification_settings 两张

**非目标**：FR-7.5 评审结论枚举 schema 化（owner 拍板话术承载，M6 随修复循环）· 模板 DELETE/PATCH 端点（§7 #9 挂账）· P12 编排/Project 设置组（M6）· Orchestrator/拆解（M6）· 桌面通知的推送服务（浏览器 Notification API 即可，无服务端推送面）· 模板库扩展内置第二款（FR-7.4 P2）。

### 2.1 执行切分（两块竖切，体例同 M2/M3/M4）

| 块 | 定位 | 模块 | 收口意义 |
| --- | --- | --- | --- |
| **M5a 第二 runtime 与配置面** | 先收 | H0 · H1 · H2 · H3 · H4 ＋ B-M5-1 | **Codex Agent 真机可创建可对话**（混 runtime 同频道互 @，契约 E2 冒烟 8 例）+ 配置面全通（技能/通知/阈值/cron）；中途变故也有完整可演示成果 |
| **M5b 模板与向导** | 块 a 收口后开工 | H5 · H6 · H7 ＋ B-M5-2 | **收口即 PRD M5 出口**（模板一键实例化，Claude 审 Codex 跑通交付）；消费块 a 产物（codex Agent/0007 表/契约登记），零回向依赖 |

- 切分依据：块 a 全是**runtime 与成员配置域**（daemon 适配器心智 + 设置管路），块 b 全是**流程资产域**（画布序列化/落地事务心智）——两类心智不混批；且出口场景"Claude 审 Codex"依赖块 a 的 codex Agent 先能跑。
- 0007 两张表在 H1 一次建齐（templates 块 a 期间空置——M3"迁移不拆两次"先例）。
- 模块编号用 **H 系列**（跳过 G——避免与 PRD 护栏 G1–G6 编号撞名）。

## 3. 现有资产盘点（拿来即用，勿重复建设；2026-07-11 双线逐项实核）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| `RuntimeAdapter` Protocol + 双层适配器骨架 | daemon adapters/base.py:23-40 / claude_code.py:75-298(进程驱动)+356-592(管理器) | 接口 E §9 冻结；**管理器（会话簿记/三档重置/崩溃熔断/去重游标）runtime 无关原样复用**——codex 只写 CodexProcess + `_launch`(392-396) 按 boot.runtime 分派 + cli.py:51 解除写死 |
| CLI 探测框架 | daemon probe.py:39-75 | `probe_claude` 注入式 runner 先例；`probe_runtimes` 注释明写"codex 归 M5"——加 `probe_codex` 并入返回即可，hello/rescan 上报链路零改动 |
| `Runtime.CODEX` 枚举 + AgentBoot.runtime | contracts enums.py:20-23 / daemon.py:147-155；hub.py:960 已填充 | **逐 Agent 携带已通**；server create/patch agent 不硬编码 claude（routes/members.py:179） |
| Codex UI 置灰/徽章 | web ComputersScreen.tsx:15-18 / MembersScreen.tsx:16 | detected_runtimes 渲染 + "(not installed)" 置灰 + `codex: 'Codex'` 标签**已实现**——探测真值一到即亮 |
| 技能白名单后端全套 | routes/members.py:285-320 / models.py:198-205 / rest.py:174 / mock 已对形状 | **M1 已 100%**：GET/PUT + R3 门 + 全量替换留痕 + AGENT_UPDATED 广播——M5 零后端修订 |
| 技能 tab（只读） | web AgentDetailScreen.tsx:139,171-186；api.ts:258 GET 已有 | 编辑态缺口：PUT mutation + 勾选/自由输入 UI |
| 模板 contracts 形状 + 落地预留 | contracts entities.py:727(TemplateRow) / enums.py:194(LandingBatchKind.TMPL) / constants.py:12(OPID_TMPL_PREFIX) | 形状冻结待收紧（H0 按 A v1.0.6 落 TemplateBody）；账本命名空间 M1 已登记 |
| 画布快照/建节点全链 | canvas/service.py:223-252(snapshot/compute_hash) / routes/canvas.py:112-230(create_node：锚点消息+L2+TaskPlan+node) | **存为模板 = 读快照序列化；实例化 = 批量复用 create_node 链**——两个方向的核心逻辑都是现成函数 |
| 落地事务器/账本 | ledger/service.py + replay.py / landing_batches 表(models.py:383) | `tmpl:<batch_id>:<node_key>` 幂等直接走 §1.4 既有机制 |
| 通知设置 contracts 形状 | contracts enums.py:242(NotificationMode) / entities.py:208(Row/Public) | 冻结即用；H0 补 Put 请求模型 + ChannelsSnapshot 第三字段 |
| activity 生成路径 | routes/messages.py:118-170 + activity/service.py:35-69(emit_activity conn 注入式，M4a F2 产物) | mode 门插在 emit 之前查询接收者设置——单点改造 |
| 频道阈值管道 | models.py:234-242 + rest.py:219-230(ChannelPatch) + routes/channels.py:134-145 | **P12 阈值后端全通**——B-M5-1 纯 UI |
| interval 塌缩重排 | reminders/interval.py `next_after` | cron 分支照此语义加（M4a 重放风暴教训单源） |
| 向导入口占位 | web SetupChecklistScreen.tsx:23 | "打开模板向导"清单项 action 未接——B-M5-2 接真 |
| 画布工具栏挂载点 | web CanvasTab.tsx:397-416(canvasbar) / NewNodeModal(465-574 弹层体例) | "模板▾"钮加这里；向导弹层照 NewNodeModal 体例 |
| 一致性测试双跑 | server tests/test_conformance_dual.py | M5 端点照 M2/M3/M4 先例扩进去（纪律 3） |

**确认缺口**（施工面）：codex.py 全套（进程驱动+cmdline+帧路由）· 0007 迁移 · templates 域 service/routes · notification-setting 端点 + mode 门 · cron 解析器 · 技能池探测（probe 扩）· mock 模板/通知形状 · web：技能编辑/频道设置弹窗/存为模板/向导三步/wsBridge 无新 case（C 零修订）。

## 4. 线 A：后端任务分解（模块 → DoD）

| # | 块 | 模块 | 内容（契约出处） | 完成判据（DoD） |
| --- | --- | --- | --- | --- |
| H0 | **M5a** | 契约登记同步（**开工第一步之一**） | contracts 包：`ENDPOINTS_M5`（B §4.12 三 + §4.5 二）、`TemplateBody`（A v1.0.6 嵌套模型）+ `TemplateCreate/TemplateInstantiate/InstantiateResult` 请求响应模型、`NotificationSettingPut`、`ChannelsSnapshot` 扩 `notification_settings`、`DetectedRuntime` 扩 `skills`、ErrorCode +NOTIF_IN_DM/+TEMPLATE_CANVAS_NOT_READY（25）、`create_reminder` 工具 cadence 描述扩 cron、`CODEX_DISALLOWED_TOOLS` 占位常量（E2 §2.5，终表 H2 实测回填）；mock 补 `GET /templates`/notification-setting 形状（纪律 4） | manifest/catalog 测试红转绿；`pnpm gen` diff 为空；mock 一致性扩展全绿 |
| H1 | **M5a** | 0007 迁移 | Alembic `0007_m5`：templates + channel_notification_settings 两张一次建齐（A §5 批次表；templates 块 a 空置）；models.py ORM + `M5_TABLES` 常量；索引按查询面最小集（templates 工作区级小表零索引可；notification_settings 复合 PK 即查询键） | 从零 `upgrade head` 与 M4 库增量升级双路绿；表结构对照测试扩展 |
| H2 | **M5a** | Codex 适配器（契约 E2 兑现，**块 a 主体**） | **先 A 级实测校准**：真机戳 `codex app-server` 帧目录，回改 06 文档 §1/§4/§5 帧名 + 关闭开放问题 #1–#4（升 v1.0.x，E v1.0.1 先例）→ `adapters/codex.py::CodexProcess`（JSON-RPC 帧路由→四类回调、相位聚合 ≤6、usage ULID、CODEX_HOME 隔离+config.toml MCP 注入+凭证物化、三档重置映射+resume 降级留痕）→ 管理器 `_launch` 按 `boot.runtime` 分派 + cli.py 解除写死 → `probe_codex`（which+version）+ **skills 探测**（claude_code 全局技能目录只读列名；codex 恒 []）并入 `probe_runtimes` | E2 §8 冒烟 8 例全绿（含混 runtime 同频道互 @、held 对 codex 生效、崩溃熔断）；探测上报后 P7 徽章真值；`CODEX_DISALLOWED_TOOLS` 终表实测回填 contracts |
| H3 | **M5a** | 通知设置端点 + mode 消费门 | B §4.5/§11.4：GET/PUT notification-setting（人类本人自治、Agent 403、dm→NOTIF_IN_DM 422、GET 无行回默认、PUT upsert 懒建）；**mode 门**插 emit_activity 之前（mute→该频道 mention activity 不生成；dm kind 不可设置故 dm activity 恒生成——必达闭环）；ChannelsSnapshot 扩 `notification_settings`（本人非默认行） | 端点逐路径（自治/403/422/默认/upsert）；mute 掐 mention 生成 + dm 恒生成 + all/mentions 后端不变的判定逐例；快照字段回归 |
| H4 | **M5a** | cron cadence | B §11.5：五段式解析器进 contracts/服务层单点（分 时 日 月 周、本地时区、无秒/年/@keyword）；`next_after` cron 分支（**塌缩语义**：now 之后首个命中，停机漏拍不逐格重放）；POST /reminders + create_reminder + LoopContractBody 三处同门校验（同种同值） | 解析逐例（合法/非法/边界日月）；创建即算 next_fire_at；触发→重排→再触发；停机跨 K 周期只触发一次；interval 全量零回归 |
| H5 | **M5b** | 模板域（保存/列表/builtin） | B §11.1：templates service+routes——POST 存为模板（读画布快照序列化 TemplateBody：仅 task 节点/占位 owner 去重/plan_skeleton 带走/pos 不入；≥1 正式节点+无草稿层否则 409 TEMPLATE_CANVAS_NOT_READY）、GET 列表（builtin 置前 body 全量）；TemplateBody 校验（model_validate+无环复用 kernel/graph+引用一致性）；**工程三角 builtin** = contracts 常量（6 节点 DAG：PM 框定→评审门→实现契约→TDD 实现→独立验收→人类终审；4 角色占位含 checker≠doer 话术、briefing、plan_skeleton 骨架）+ server 启动 upsert 不可删改 | 保存逐路径（约束 409 二值/占位提取/骨架带走）；builtin 启动幂等 upsert；列表排序；无环与引用校验红例 |
| H6 | **M5b** | 实例化事务器 | B §11.2：`POST /templates/{id}/instantiate`——role_mapping 全覆盖校验（缺失 422 details.missing、null=待认领）→ 单事务落地批（kind=tmpl、`tmpl:<batch_id>:<node_key>` 幂等、逐节点复用 create_node 全链 + edges 连边）→ briefing 系统消息 @映射角色（mention 唤醒即开工）→ 既有事件广播（**零新增 WS 事件**） | 实例化端到端（节点/边/任务/TaskPlan 初稿/锚点消息齐全）；幂等重试恰一批；blocked/gating 即时生效断言（M3b 机制零改动）；Idempotency-Key 复用账本 |
| H7 | **M5b** | 端到端实机 verify（**块 b 收口 = PRD M5 出口**） | 真机（隔离库+独立端口+**真 codex CLI**，owner 已装）：创建 codex Agent（P7 探测徽章→P13 弹窗 runtime 可选）→ 工程三角向导实例化（实现=Codex、评审=Claude——跨 runtime 默认，同 runtime warning 验证）→ briefing 落频道 → **Codex Agent 认领实现任务交付 → Claude Agent 评审（话术结论）→ 人类终审 done**；顺带：技能池勾选授予、频道 mute 后 mention 不进 Activity、cron reminder 到点触发、held 对 codex 生效复验 | §9a+§9b 清单收口 + 截图/证据归档 `docs/verify/M5-EVIDENCE.md` |

**推进顺序**：**块 M5a**：H0 ∥ H1 并行（文件域不相交：contracts 包 / migrations）→ H2（消费 H0 常量占位；**A 级实测校准最先做**——帧名不确定性是全里程碑最大风险，先戳真机）∥ H3 ∥ H4（三者文件域不相交：daemon / routes+activity / reminders，可并行）；**块 M5b**：H5 → H6 串行（实例化消费保存的 body 语义）→ H7 收口。

## 5. 线 B：前端任务分解（模块 → DoP）

> 沿用设计线 verify SOP：优先消费归档稿 `docx_agenthub/04-设计稿/afterglow-ds/previews/`（P6 技能页签 / P12 设置 / P13a 模板向导+保存模板弹窗均有稿）→ playwright 1440×900 对照 → 复发点自查。

| # | 块 | 模块 | 内容 | 完成判据 |
| --- | --- | --- | --- | --- |
| B-M5-1 | **M5a** | 技能编辑 + 频道设置弹窗 + cron 显示 | ① P6 技能页签编辑态：候选池 ∪ 已授予勾选列表（池 = 所在机器该 runtime 的 detected skills；池外已授予仍显示可移除；自由输入仍允许；codex Agent 显示"该 runtime 无技能机制"引导文案）+ `putAgentSkills` mutation + R3 权限位（创建者/admin 才见编辑态）；② **频道设置弹窗**（P12 频道级，⋯菜单入口）：基本/通知（all/仅@/静音）/提醒阈值/护栏阈值四组（阈值 = 挂账 P12 UI 一并收，ChannelPatch 管道现成；编排/Project 组不做）+ 通知徽标按 mode 渲染（mentions=仅 @ 亮、mute=弱化）+ 桌面通知按 mode（交互 §14，工作区总开关已有列）；③ P6 Reminders 页签 cron 原样 mono + 人读预览 | 行为测试（勾选/自由输入/权限位/mode 切换/dm 无设置入口）+ 屏对照截图 |
| B-M5-2 | **M5b** | 存为模板 + 向导三步 | ① 画布工具栏「模板▾」（存为模板：≥1 正式节点才可用/草稿层 disabled+tooltip；保存弹窗 = 名称/描述/**角色占位提取表**（owner 去重可改名/无 owner 归待认领）/包含节点勾选——P13 保存模板稿）；② **模板向导三步**（P13a 稿）：选模板（卡片+DAG 缩略图）→ 角色映射（每占位下拉选成员或「新建」跳创建 Agent 弹窗回填；**评审+实现同 runtime 就地 warning 不阻塞**——FR-7.3 唯一落点）→ 预览简报与画布 → 实例化（落地后跳画布定位）；③ SetupChecklist 003「打开模板向导」接真 | 行为测试（约束 disabled/占位改名/同 runtime warning/新建回填/实例化跳转）+ 屏对照截图 |

## 6. 纪律（沿用 M2–M4 八条，本里程碑强调两点）

1. 契约 ↔ manifest 双向同步（H0 收口中间态；**H2 实测回改 06 文档后同步升版**——外部依赖校准是本里程碑特有义务）。
2. 生成物只经脚本重生成，diff 为空守门。
3. 一致性测试套件双跑复用：M5 端点直接扩进 `test_conformance_dual.py`。
4. mock 是形状源不是逻辑源：模板序列化/实例化事务/mode 门/cron 解析只活在真 server。
5. 每完成一个模块：更新 M5-DEV-PLAN §进度表 + [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)；阶段结论沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问；已拍板勿再问（§7 #1–#4 即已拍板项）。
7. 值域/判定语义只写一处：TemplateBody 校验、mode 消费门、cron 解析全走 contracts 常量/服务层单点，前端不复制判定。
8. **runtime 无关性纪律**（本里程碑新增）：管理器骨架/输入编码/工具目录/护栏语义对两 runtime **必须同一份代码**——凡是想在 server 或编码层写 `if runtime ==` 的地方，先回头看契约 E/E2 边界表（适配器内才允许 runtime 特化）；混 runtime 冒烟（E2 §8.8）是这条纪律的守门测试。

## 7. 本任务书裁决（实现按此执行；#1–#4 = owner 拍板 2026-07-11，其余 owner 可否决，否决处升契约版本回改）

| # | 裁决 | 依据 |
| --- | --- | --- |
| 1 | **Codex 真机可用**（owner 确认已装已登录）——出口按真机 Codex 验收写，无降级路径；06 帧名开工 A 级实测校准并回改文档 | owner 2026-07-11；E2 AI 实现说明 |
| 2 | **FR-7.5 评审结论枚举不进 M5**——工程三角评审门 = 话术承载（builtin roles.description + briefing 约定结论格式）；schema 化归 M6 随修复循环 | owner 2026-07-11；B §11.2 #6 |
| 3 | **cron cadence 收进 M5a**：五段式/本地时区/塌缩式重排；解析单点 | owner 2026-07-11；B §11.5 |
| 4 | **技能候选池 = daemon 探测上报**：`detected_runtimes[].skills` 扩形状零迁移；**列出≠授予**（R6 不违反）；自由输入保留 | owner 2026-07-11；A v1.0.6 / D v1.0.2 / B §11.3 |
| 5 | DM 必达：dm 频道无通知设置面（GET/PUT → 422 NOTIF_IN_DM）；dm activity 恒生成 | B §11.4 #2；PRD FR-4.7 + P14 无设置入口 |
| 6 | 通知 mode **只作用人类通知面不作用 Agent 投递层**；后端唯一消费点 = mute 掐 mention activity 生成；未读**事实**与显示策略解耦（mode 不动 read_positions） | B §11.4 #3/#4；两个作用层纪律第三面 |
| 7 | **零新增 WS 事件**（契约 C 保持 v1.0）：模板/通知无实时事件面，列表 REST 拉取、PUT 后操作方本地更新 | B §11.2 #4；单人类工作区收益不抵事件面扩张 |
| 8 | instantiate `role_mapping` 收窄为 `member_id \| null`（**移除内联 create**）——向导「新建」= 前端走既有创建 Agent 弹窗回填（无 Computer 引导链不塞事务） | B §4.12 v1.3 收窄 |
| 9 | 模板 = 工作区级资产；builtin 启动 upsert 不可删改；**无 DELETE/PATCH 端点**（列表污染单人类可接受，挂账 M6+） | B §11.1 #3 |
| 10 | codex **一 Agent 一进程**（不共享 app-server 进程）：配置隔离/崩溃域/熔断骨架复用三重理由 | E2 §1.1 |
| 11 | codex 技能候选池恒 `[]`、白名单不物化（UI 引导文案而非空表）；后续 codex 引入等价机制再升版 | E2 §7.5 / B §11.3 #3 |
| 12 | M5 工具组为空（`COAGENTIA_MCP_TOOLS` 零新增，连续第三个里程碑）；唯一变化 = create_reminder cadence 值域 | E v1.4 |
| 13 | P12 **频道级**设置弹窗随 B-M5-1 一并收（基本/通知/提醒/护栏四组；管道 M1 全通纯 UI）；编排/Project 组归 M6；工作区级 /settings 页不动 | M4 挂账；P12 原型频道级表 |

## 8. 挂账（承接 CURRENT-HANDOFF §5；勿当漏项重新发明）

| 出处 | 问题 | 归属 |
| --- | --- | --- |
| M4b code-review #8/#9 | `_post_system_message` / `_channel_human_members` 跨模块 DRY 债（非 bug） | 顺手小批；**H6 briefing 系统消息若复用骨架则顺路评估收敛** |
| M4b 观察 | held 卡升级态倒计时显示「重评估中…」边角 | 观察项 |
| M2 挂账 | hub usage.batch 逐事件 SELECT；search 双 MATCH+LIKE 扫描 | 性能小批 |
| M2 观察 | `task #n` refs 无 UI 消费面 | 顺手评估 |
| M2 挂账 | P11/P3 看板双实现抽 `<TaskBoard>` | 顺手评估 |
| M3 观察 | messages_fts 键于 rowid，VACUUM 失同步 | 观察项 |
| M1 遗留 | OAuth 冷启动复验 | H7 真机可顺路 |
| ~~M4 新增~~ | ~~P12 阈值设置 UI~~ | **B-M5-1 收**（§7 #13） |
| **本任务书新增** | 模板 DELETE/PATCH 端点（列表污染治理） | M6+ |
| **本任务书新增** | 工作区级 /settings 独立页（P12 工作区级组：基本/成员/Onboarding/外观/通知总开关） | 顺手评估（部分字段 PATCH /workspace 管道已通） |

## 9. M5 出口验收清单（按块分组；两块全绿即里程碑收口）

### 9a. 块 M5a「第二 runtime 与配置面」清单

- [ ] 1. H0 契约登记：ENDPOINTS_M5 / TemplateBody 及请求响应模型 / NotificationSettingPut / ChannelsSnapshot 扩字段 / DetectedRuntime.skills / 错误码 25 / cron 工具描述 / mock 形状，catalog 与 `pnpm gen` 两跑一致
- [ ] 2. Alembic `0007_m5`：两张表从零与增量双路绿；M5_TABLES 对照测试
- [ ] 3. H2 实测校准：06 文档开放问题 #1–#4 关闭并升版；CODEX_DISALLOWED_TOOLS 终表回填
- [ ] 4. Codex 冒烟 8 例（E2 §8）：启动就绪/完整对话/Restart resume（或降级留痕）/二三档/held 生效/熔断/帧防腐/**混 runtime 同频道互 @**
- [ ] 5. 探测链路：probe_codex + skills 候选池上报 → P7 徽章真值 → P13 创建弹窗 codex 可选
- [ ] 6. 通知设置逐路径：自治/Agent 403/dm 422/默认懒建/mute 掐 mention 生成/dm 恒生成/all-mentions 后端不变/快照字段
- [ ] 7. cron：解析红绿例/创建即算/触发塌缩重排/停机跨周期恰一次/interval 零回归
- [ ] 8. B-M5-1：技能编辑（池∪已授予/权限位/codex 引导文案）+ 频道设置弹窗四组 + 徽标按 mode + cron 人读预览，行为测试 + 截图
- [ ] 9. 块 a 守门：后端/前端全量测试、typecheck（pyright 0）、ruff、gen 确定、双侧 build 全绿；交接文档同步（纪律 5）

### 9b. 块 M5b「模板与向导」清单（全绿 = **PRD M5 出口达成**）

- [ ] 10. 模板保存逐路径：快照序列化（仅 task 节点/占位去重/骨架带走/pos 不入）/约束 409 二值/builtin 启动幂等 upsert/无环与引用红例
- [ ] 11. 实例化端到端：role_mapping 校验（缺失 422/null 待认领）/单事务落地批幂等/节点边任务契约锚点齐全/briefing @角色/blocked-gating 即时生效/Idempotency-Key
- [ ] 12. B-M5-2：存为模板弹窗（约束 disabled/占位提取表）+ 向导三步（DAG 缩略图/**同 runtime warning**/新建回填/预览/实例化跳转）+ SetupChecklist 003 接真，行为测试 + 截图
- [ ] 13. **PRD M5 出口真机实证**：工程三角向导实例化（实现=Codex、评审=Claude）→ briefing 开工 → **Codex 交付 → Claude 评审 → 人类终审 done** 全链真机；顺带技能授予/mute 生效/cron 触发/held-codex 复验；全程 WS 无刷新；截图归档 `docs/verify/M5-EVIDENCE.md`
- [ ] 14. 终收口守门：全量测试绿（基线只增不减）；M5 阶段结论写入 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本任务书移入 archive/（README 维护约定 3）

## 10. 第一步建议

块 M5a 从 **H0 ∥ H1 并行**开工（contracts 包 / migrations 文件域不相交），**H2 的 A 级实测紧随其后优先做**——`codex app-server` 帧目录是全里程碑唯一的外部不确定性（06 文档五项开放问题全系于此），先真机戳帧校准文档，再写 CodexProcess 就是照契约填空；H3/H4 与 H2 文件域不相交可并行。B-M5-1 契约形状就位（H0）后即可吃 mock 开工。**块 M5b 不与块 a 交错**——等 §9a 全绿再动（出口场景依赖 codex Agent 真跑），保持"一块 = 一个可交接的收口"。
