# M2 任务与看板 —— 任务书（M2-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-09，M1 实现与 hardening 合并 `main`（`f2c993f`）之后 |
| 用途 | **M2 里程碑的唯一任务书入口**：把 L1 任务协作装进系统。前置任务书 [M1-IMPL-HANDOFF.md](archive/M1-IMPL-HANDOFF.md) 已完成归档 |
| 上游事实源 | [engineering_docs/](../../../engineering_docs/README.md) 五契约（本批修订：**A v1.0.2 / B v1.1 / E v1.1**；C、D 无变更）· [CoAgentia-PRD.md](../../../docx_agenthub/CoAgentia-PRD.md) §4.2 状态机 / T1–T8 / FR-4.6·4.8 / FR-5 / FR-8 / §8 里程碑 · [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)（M1 收口态） |
| 执行计划 | 待建：开工首会话按本任务书建 `M2-DEV-PLAN.md`（体例同 [M1-DEV-PLAN.md](../../../M1-DEV-PLAN.md)：步骤分解、会话切分、测试策略、验收映射） |
| 出口标准 | **PRD M2 出口：复刻"番茄钟"式小项目全流程——人发任务 → Agent 认领交付**（§9 逐条验收清单） |

---

## 0. 一句话目标

M1 证明了"Agent 能对话与产出"；M2 把协作装进**系统对象**：消息可转任务、任务有编号/5 态/唯一 owner、每笔变更进 task_events 账本、看板与聚合板可视、文件页签/Activity/全局搜索让一切可找回——达到 Raft 水平的 L1 任务协作（D1 三档中的 L0/L1；L2 契约留给 M3）。

## 1. 契约修订摘要（本任务书立项时已完成的文档变更，实现前先读）

| 契约 | 版本 | 变更 |
| --- | --- | --- |
| A 实体表 | v1.0.1 → **v1.0.2** | `task_events` 增可空列 `owner_member_id` + kind 目录增 `assign`；`activity_items.kind` 目录增 `dm`。均为未迁移表，符合 A §5 冻结策略 |
| B REST | v1.0.2 → **v1.1** | **新增 §9 任务域规范条文**（状态机合法边表 / claim·unclaim·assign 语义 / 编号与锚点 / as_task 原子性 / `task #n` 解析 / 搜索形状 / Activity 生成规则 / TaskDetail）；新端点 `GET /channels/{id}/files`（39→40）；新错误码 `TASK_TRANSITION_INVALID`（21→22）；§4.7 里程碑切分（force-start / 契约端点归 M3）；§6 重同步清单补 `GET /activity` |
| C WS | 无变更 | `task.created/updated`、`activity.created/done`、`token_usage.reported` 首版已登记（C §6.4），M2 只是开始发射 |
| D daemon | 无变更 | 投递触发器不变（任务锚点是普通频道消息，@mention/DM 触发即覆盖）；usage 任务归属按 E §7.4 既定机制在 server 落库处激活 |
| E 适配器 | v1.0.1 → **v1.1** | §3 MCP 工具增补 M2 组：`list_tasks` / `get_task` / `claim_task` / `unclaim_task` / `set_task_status` / `search` + `send_message` 增 `as_task` 参数 |

> **纪律 1 的中间态说明**：契约文档已先行，`packages/contracts` 包与 manifest 测试的同步是本任务书 **C0 模块 = 开工第一步**，在任何业务实现之前完成。

## 2. 范围与非目标（勿扩界）

**范围**（PRD §8 M2 行）：

- L1 任务：**两条创建途径**（Convert to Task / composer As Task；第三途径"画布建节点"随 M3）+ claim/unclaim/assign + 5 态状态机 + task_events 账本 + 频道内自增编号
- 看板 P3（每频道，5 态分列，Done/Closed 默认折叠）+ 工作区聚合板 P11（T8）
- 文件页签 P4（`GET /channels/{id}/files`）
- Activity P9（mention + dm 两类生成，三过滤，Mark as done）
- 全局搜索 P10（Ctrl+K，FTS5，三分组）
- M2 建表批次（A §5）：`tasks` · `task_events` · `message_task_refs` · `activity_items` · `messages_fts`
- Agent 任务参与面：MCP 工具 M2 组（E §3）
- usage 任务归属富化激活（E §7.4：thread_root_id 命中锚点 → task_id）

**非目标**：L2 契约与 T7 校验（M3）· 画布与 blocked/force-start（M3）· freshness/HeldDraft（M4）· 沉默提醒 D5（M4——`silence_override_h` 列 M2 就位但无消费方）· 每频道通知设置（M5）· `GET /usage` 三层聚合（M6）· 多人可见性过滤（多人化后置）。

### 2.1 执行切分（两块竖切，owner 确认 2026-07-09）

| 块 | 定位 | 模块 | 收口意义 |
| --- | --- | --- | --- |
| **M2a 任务闭环** | 先收 | C0 · C1 · C2 · C3a · C5 · C7 ＋ B-M2-1 · B-M2-2 | **收口即 PRD M2 出口达成**（番茄钟全流程）；中途变故也有完整可演示成果 |
| **M2b 发现与聚合面** | 块 a 收口后开工 | C3b · C4 · C6 ＋ B-M2-3 · B-M2-4 | M2 里程碑全绿；只消费块 a 产物（C1 表、C2 端点），零回向依赖，可换会话/执行者冷启动 |

- 切分依据：块 a 全是并发与状态机硬骨头（竞态/逐边/幂等），块 b 全是检索与生成规则（FTS/activity）——两类心智不混批。
- **C6（usage 富化）是浮动件**：只依赖 C1；归块 b 因其 UI 消费方（token 徽章）非出口必需，块 a 期间看板徽章优雅缺席（同 M1 task chips 先例）；哪块顺手哪块收。
- 批 6 四屏维持伴随定位，可挂块 b 尾部或独立小批，不进任一块的门。

## 3. 现有资产盘点（拿来即用，勿重复建设）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| M2 表形状 | `packages/contracts` entities.py | TaskRow/TaskEventRow/MessageTaskRefRow/ActivityItemRow + Public **已冻结落地**（34 表 manifest 测试钉死）；本批 A v1.0.2 的 owner_member_id 列待 C0 补 |
| as_task 请求形状 | `packages/contracts` rest.py | `AsTask` 模型与 `MessageCreated.task` 位 **M1 已就位**（当时不激活） |
| 任务域 WS 事件 | `packages/contracts` ws.py | task.*/activity.*/token_usage.reported **已登记**，Envelope 直接复用 |
| 错误码 | rest.py `ErrorCode` | CLAIM_RACE / TASK_IN_DM / NOT_TOP_LEVEL_MESSAGE 已有；缺 TASK_TRANSITION_INVALID（C0 补） |
| mock 任务读端点 | `apps/mock-server` | `GET /api/tasks`（Page[TaskPublic]）已供 B 线；fixtures 番茄钟任务 #1–#7 就是 M2 验收同款数据 |
| 前端任务消费面 | `apps/web` | TaskChip / useTasks / useUsageByTask（WS 累加）/ board tab 占位路由 / Composer As Task 复选 / `?task=` 深链 **全部已就位**——M2 主要是"接真"不是"新建" |
| server 接缝 | `apps/server` | `tasks/` 空目录（00 §3 模块位）；messages 路由 @mention 解析可扩展 task #n；usage.batch 落库点（DaemonHub）即富化挂点 |
| daemon 侧 usage 提示 | `apps/daemon` adapters/frames.py | usage 事件已带 channel/thread 归属提示（E §7.4），server 侧富化即激活 |
| 一致性测试双跑 | `apps/server/tests/test_conformance_dual.py` | M1 既有机制，M2 端点照样扩进去（纪律 3） |

## 4. 线 A：后端任务分解（模块 → DoD）

| # | 块 | 模块 | 内容（契约出处） | 完成判据（DoD） |
| --- | --- | --- | --- | --- |
| C0 | **M2a** | 契约登记同步（**开工第一步**） | contracts 包：`ErrorCode`+TASK_TRANSITION_INVALID、`ENDPOINTS_M2` 元组（§4.7/4.8 M2 集 + files 页签端点）、`TaskEventRow`+owner_member_id、ActivityItem kind 目录+dm、task_events kind+assign、**`TASK_TRANSITIONS` 合法边常量**（B §9.1 机读化）、TaskDetail/SearchResponse/TaskStatusChange 等请求响应模型、MCP 工具目录 M2 组常量；mock 补 M2 读端点形状（业务逻辑不进 mock，纪律 4） | manifest/catalog 测试红转绿；`pnpm gen` 重生成 diff 为空；mock 一致性测试扩展后全绿 |
| C1 | **M2a** | M2 建表迁移 | Alembic `0002_m2`：tasks / task_events / message_task_refs / activity_items + `messages_fts`（FTS5 external-content，随 messages 插入同步——A §4.2）**五对象一次迁移建齐**（activity_items/messages_fts 在块 a 期间先空着，迁移不拆两次）；**task_events 进不可变触发器清单**（A §1 六表之一，M1 只建了五张的触发器） | 从零 `upgrade head` 与 M1 库增量升级都绿；表结构对照测试扩展；触发器拒 UPDATE/DELETE；FTS 与 messages 同步测试 |
| C2 | **M2a** | 任务域端点 | B §4.7 M2 集 + §9.1–9.4：建号（next_task_number 同事务）、convert（幂等返回既有）、as_task 原子、claim/unclaim/assign（联动与留痕按 §9.2 表）、status（合法边表 + 幂等 200 + TASK_TRANSITION_INVALID）、GET /tasks 过滤排序、TaskDetail、PATCH title/阈值 | 一致性套件双跑扩 M2 全绿；**并发 claim 恰一成功**（线程池竞态测试）；状态机**逐边测试**（合法边全过 + 非法边全拒 + 同态幂等 + done 终态）；task_events 留痕断言逐 kind |
| C3a | **M2a** | 消息联动·任务引用 | B §9.5：`task #n` 解析落 message_task_refs（仅当前频道）；as_task 的 message.created+task.created 广播序 | 解析测试（命中/跨频道不命中/未命中保文本）；WS 事件序断言 |
| C3b | **M2b** | 消息联动·Activity 生成 | B §9.7：mention→activity(mention) + DM→activity(dm)（人类接收者，不给作者与 Agent） | activity 生成与三过滤 + done 幂等测试 |
| C4 | **M2b** | 文件页签 + 搜索 + Activity 端点 | `GET /channels/{id}/files` 倒序分页；FTS5 检索 + `GET /search` 三分组 + snippet（«» 标记）；`GET /activity` + `POST /activity/{id}/done` | 端点测试全绿；**中文检索质量实测**并把结论写回契约 A §10.4（unicode61 够用或换 trigram——挂账收口） |
| C5 | **M2a** | MCP 任务工具 | E §3 M2 组六工具 + send_message as_task 参数（daemon `adapters/mcp.py` 纯代理纪律，零业务规则）；`DISALLOWED_TOOLS` 复核（内置 TaskCreate/TaskList 等 v1.0.1 终表已禁，无重叠）；`search` 工具随块 b 端点就绪后补接（工具目录 C0 一次登记） | 工具往返测试（注入 HTTP 桩）；错误结构化透传断言（CLAIM_RACE / TASK_TRANSITION_INVALID）；真 CLI 冒烟扩 1 条：Agent list_tasks→claim→set_status（`COAGENTIA_SMOKE=1`） |
| C6 | **M2b**（浮动） | usage 归属富化激活 | usage.batch 落库时 thread_root_id 命中 `tasks.root_message_id` → 写 task_id（E §7.4 既定；DaemonHub 上报处理内）；`token_usage.reported` 事件带 task_id | 富化单测（命中/未命中/提示缺失三路）；TaskDetail.usage 聚合测试；tasksReporting 口径不虚报 |
| C7 | **M2a** | M2a 端到端集成（**块 a 收口 = PRD M2 出口**） | **番茄钟场景**真机脚本：人在 #build 发消息 As Task → task #n(todo) 广播 → Agent（MCP）list_tasks→claim（→in_progress）→ 线程发进度 → 写 Home + upload_file 绑定线程消息 → set_task_status(in_review) → 人看板置 done；全程 WS 驱动无刷新 | §9a 清单收口 + 截图/录屏归档 `docs/verify/` |

**推进顺序**：**块 M2a**：C0→C1→C2 串行（地基链）→ C3a/C5 并行 → C7 收口；**块 M2b**（块 a 收口后开工）：C3b/C4 并行，C6 浮动（只依赖 C1）。

## 5. 线 B：前端任务分解（模块 → DoP）

> 每屏沿用设计线 verify SOP：消费归档稿 `docx_agenthub/04-设计稿/afterglow-ds/previews/` → token 零发明 → playwright 1440×900 截图对照 → 复发点 5 条自查。窄屏（390px）回归沿用 M1 hardening 的抽屉布局。

| # | 块 | 模块 | 内容 | 完成判据 |
| --- | --- | --- | --- | --- |
| B-M2-1 | **M2a** | P1/P5 任务面接真 | `api.tasks()` 撤 IS_MOCK 门转正式；TaskChip 真数据 + `?task=` 深链回归；P5 线程面板**状态操作条**激活（claim / 流转按钮 + CLAIM_RACE toast"已被 @Pat 认领"）；Composer As Task 消费原子响应（Ctrl+Shift+Enter） | 行为测试（wsBridge task.* 已有底子）+ 屏对照 |
| B-M2-2 | **M2a** | P3 看板 + P11 聚合板 | 5 态分列、Done/Closed 折叠、卡片拖列 = status 写（非法边就地弹回 + toast）；聚合板 Board/List 双视图 + Channel/Creator/Assignee 过滤（T8）；WS task.* 实时移列（交互 §5.1 240ms）；token 徽章块 a 期间优雅缺席（C6 随块 b） | playwright 对照归档稿 P3/P11 |
| B-M2-3 | **M2b** | P4 文件 + P8 成员 | 文件页签分页 + inline 预览类型分流（图片/PDF/MD/文本/CSV，FR-4.8）+ 消息流附件卡补齐（M1 遗留并入）；P8 成员表（含角色徽章） | 对照归档稿 P4/P8 |
| B-M2-4 | **M2b** | P9 Activity + P10 搜索 | 三过滤 + 逐项 done + 置顶区（M2 恒空，结构就位）；Ctrl+K 覆盖层三分组 + 前缀解析（from:/in:/task:）+ 命中跳原文高亮（«» snippet 渲染） | 对照归档稿 P9/P10；快捷键行为测试 |
| （伴随） | — | 批 6 四屏 P12/P13a/b/P14/P15 | **不进 M2 出口门**（M1-DEV-PLAN 既定"跑在 M2 前面即可"的遗留），挂块 b 尾部或独立小批；P12 频道阈值段消费 PATCH /channels 既有端点 | 同 SOP |

## 6. 纪律（沿用 M1 六条，唯一新增第 7）

1. **契约 ↔ manifest 双向同步**（本批文档已先行，C0 收口中间态）。
2. **生成物只经脚本重生成**，diff 为空守门。
3. **一致性测试套件双跑复用**：M2 端点直接扩进 `test_conformance_dual.py`。
4. mock 是形状源不是逻辑源：建号/竞态/边表/FTS 只活在真 server。
5. 每完成一个模块：更新 M2-DEV-PLAN §进度表 + [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)；阶段结论沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问；已拍板勿再问。
7. **状态机语义只写一处**：合法边表 = contracts `TASK_TRANSITIONS` 常量，server 校验与前端防呆（拖列禁用/按钮置灰）共同消费，不得两侧各写一份字面量。

## 7. 本任务书裁决（实现按此执行；owner 可否决，否决处升契约版本回改）

| # | 裁决 | 依据 |
| --- | --- | --- |
| 1 | claim 联动 todo→in_progress、unclaim 仅限本人并联动回 todo、assign 不动 status | PRD §4.2 边标签的直译（B §9.2） |
| 2 | 同态流转幂等 200、convert 已转消息幂等返回既有任务 | 乐观重试友好；root_message_id UNIQUE 天然去重 |
| 3 | 非法边独立错误码 TASK_TRANSITION_INVALID（不混 VALIDATION_FAILED） | toast 文案与前端分支需要机器码（B §1 错误形状原则） |
| 4 | task_events 增 owner_member_id 列 | assign/claim 此前无新 owner 审计载体，T5"谁何时"精神的补全 |
| 5 | Activity M2 生成 mention + dm 两类；**Agent 成员不生成** | Activity 是人类聚合面；Agent 触达已有投递引擎（D §8），双写即重复 |
| 6 | `task #n` 仅解析当前频道 | 编号频道内自增（T1），跨频道引用无定义 |
| 7 | title 缺省 = 锚点 body 首个非空行剥 Markdown 前缀，>80 截断 | FR-5.1 三途径都可不填 title，需确定性缺省 |
| 8 | 批 6 四屏为伴随任务，不进 M2 出口门 | PRD M2 出口不含设置/私信/成员面板；M1-DEV-PLAN 既定节奏 |
| 9 | force-start 不设 MCP 工具（连同端点归 M3） | C3 仅人类，Agent 永不可调，无需工具位 |

## 8. 挂账（勿当漏项重新发明）

| 出处 | 问题 | 归属 |
| --- | --- | --- |
| A §10.4 / B §8.2 | FTS5 中文分词质量 | **C4 实测收口**（本里程碑必须给结论） |
| M1 遗留 | flaky `test_backlog_excludes_agent_own_messages` | 顺手硬化，非出口门 |
| CURRENT-HANDOFF P1 | pyright 109 既有错误 | 独立批处理，**勿混入 M2 业务提交** |
| CURRENT-HANDOFF P0 | 真实双 Agent OAuth 冷启动复验 | C7 真机场景可顺路执行（双 Agent 版番茄钟），结论单独记录；M2 出口本身单 Agent 即满足 |
| M1 遗留 | P1 消息流附件渲染缺失 | **并入 B-M2-3**（文件页签同批补消息内附件卡） |
| B3 画布骨架 | React Flow 静态骨架 | 维持 M1-DEV-PLAN 定位：M3 前完成即可，不进 M2 门 |

## 9. M2 出口验收清单（按块分组；两块全绿即里程碑收口）

### 9a. 块 M2a「任务闭环」清单（全绿 = 块 a 收口 = **PRD M2 出口达成**）

- [ ] 1. Alembic `0002_m2` 落库：4 表 + messages_fts + task_events 不可变触发器；从零与增量升级双路绿（C1）
- [ ] 2. contracts 包 M2 登记完成：ENDPOINTS_M2 / 错误码 22 / TASK_TRANSITIONS / TaskDetail 等模型，manifest 测试与 `pnpm gen` diff 为空（C0）
- [ ] 3. 一致性套件双跑（mock + 真 server）覆盖 **M2a 任务域端点**全绿（C2；M2b 端点部分随块 b 补足）
- [ ] 4. as_task 与 convert 原子建任务：`{message, task}` 一次响应、编号频道内自增且并发不重号、DM/线程拒绝路径结构化（C2/C3a）
- [ ] 5. **并发 claim 恰一成功**，败者 409 CLAIM_RACE {current_owner}；claim/unclaim/assign 留痕逐 kind 断言（C2）
- [ ] 6. 状态机逐边验收：合法边全过、非法边全拒 TASK_TRANSITION_INVALID、同态幂等、done 终态；Agent 经 MCP 与人同权实证（R4）（C2/C5）
- [ ] 7. `task #n` 解析落 refs + 前端 TaskChip 深链跳转（C3a/B-M2-1）
- [ ] 8. 看板 P3 与聚合板 P11：5 态分列、拖列改状态、WS 实时移列（playwright 实证）（B-M2-2）
- [ ] 13. **番茄钟全流程真机实证**（PRD M2 出口）：人发任务 → Agent claim → 线程汇报 → 文件产出 → in_review → 人置 done，全程无刷新；录屏 + 截图归档 `docs/verify/`（C7）
- [ ] 14a. 块 a 守门：全量 pytest + Web vitest + 双侧 typecheck + ruff 全绿；三处交接文档同步（纪律 5）

### 9b. 块 M2b「发现与聚合面」清单（全绿 = M2 里程碑收口）

- [ ] 3b. 一致性套件双跑补齐 **M2b 端点**（files/search/activity）全绿（C4）
- [ ] 9. 文件页签 P4 分页 + inline 预览 + 消息流附件渲染补齐（C4/B-M2-3）
- [ ] 10. Activity P9：mention/dm 生成、三过滤、Mark as done、WS 实时（C3b/B-M2-4）
- [ ] 11. 搜索 P10：Ctrl+K 三分组、FTS 命中跳原文高亮；中文检索质量结论写回契约 A §10.4（C4/B-M2-4）
- [ ] 12. usage 归属富化：任务线程内 turn 的 token 落到 task_id，TaskChip 徽章真数据（C6，浮动件）
- [ ] 14b. 终收口守门：全量测试绿；M2 阶段结论写入 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本任务书移入 archive/（README 维护约定 3）

## 10. 第一步建议

块 M2a 从 **C0 + C1** 开工（契约登记 + 建表）：C0 把本批文档修订收进 contracts 包让 manifest 回到"文档=代码"一致态，C1 是一切业务端点的地基（五对象建齐，块 b 直接复用）；同一会话顺手把一致性套件的 M2 端点形状占位铺好（纪律 3）。B 线 B-M2-1 可即刻开工吃 mock（`/api/tasks` mock 已在供数据）。**块 M2b 不与块 a 交错开工**——等块 a 的 §9a 清单全绿再动，保持"一块 = 一个可交接的收口"。
