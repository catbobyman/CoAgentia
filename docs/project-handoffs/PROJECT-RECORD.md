# CoAgentia 项目记录

> 本文汇总已过时的交接文档，保留项目演进事实，但不作为当前任务入口。当前状态以 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) 为准。

## 1. 设计与品牌阶段

来源：[archive/HANDOFF.md](archive/HANDOFF.md)

- 完成 Afterglow 设计系统及七批设计稿，建立品牌、交互与视觉基线。
- 项目名称由 AgentHub 统一调整为 CoAgentia，并记录镜像设计稿的同步纪律。
- 当时“仅有文档和设计稿、尚无产品代码”的判断只适用于该阶段，现已失效。

## 2. M1 契约阶段

来源：[archive/M1-HANDOFF.md](archive/M1-HANDOFF.md)

- 冻结实体模型、REST API、浏览器 WebSocket、daemon-server 协议和 Claude Code 适配器五类契约。
- 建立 Pydantic contracts、fixtures、mock server、TypeScript 生成链路和 P1 会话屏形状验证。
- 该任务书已完成归档；其中“下一步从契约实施开工”的描述已被后续实现结果取代。

## 3. M1 实现阶段

来源：[archive/M1-IMPL-HANDOFF.md](archive/M1-IMPL-HANDOFF.md)

- 完成 A1-A8 后端链路：数据层、REST、浏览器 WS、daemon 网关与对账、daemon 本体、Claude Code 适配器和端到端集成。
- 完成 B1-B2a 与 B4 前端链路，交付七屏、同源部署和关键会话流程。
- 实机完成两个 Claude Haiku Agent 的频道对话、文件产出与 reminder 验收，M1 出口清单收口。
- 该任务书中的未勾选开工指令和阶段性待办均为历史快照，不再代表当前进度。

## 4. M1 首次收口快照

来源：[archive/SESSION-HANDOFF.md](archive/SESSION-HANDOFF.md)

- 记录 M1 实现收口、代码审查修复和 `230 passed, 2 skipped` 的阶段性验证结果。
- 当时建议下一步合并 `m1-impl`、补 B2b/B3 或进入 M2。
- 该快照早于随后进行的 M1 hardening；测试数字、风险列表和下一步已由当前交接更新。

## 5. M1 Hardening 与实机复验

来源：[CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) 与 [修复报告](../m1-review-fixes-20260709/FIX-REPORT.md)

- 针对实机验证和代码审查发现的七项问题完成修复，覆盖进程参数、API/WS 基址、消息/文件一致性、错误传播及回归测试。
- 完成桌面与移动视口的真实浏览器复验，并保存截图和 trace 证据。
- 当前验证基线：Python `238 passed, 2 skipped`，Web `10 passed`，TypeScript typecheck/build 与 Ruff 通过。
- Pyright 仍有 109 个既有错误，作为独立技术债保留。
- 修复以 `351684a` 提交，并通过合并提交 `f2c993f` 合入 `main`。

## 6. M2 任务与看板（含二轮 review 修复）

来源：[archive/M2-HANDOFF.md](archive/M2-HANDOFF.md) · [M2-DEV-PLAN.md](M2-DEV-PLAN.md) · [二轮证据](../verify/M2-REVIEW2-EVIDENCE.md)

- 两块竖切完成 L1 任务协作：C0-C7 后端（契约登记/0002 建表/任务域 8 端点/task#n 解析/Activity 生成/files·search·activity 端点/MCP 六工具/usage 富化）+ 前端 B 线（P1/P5/P3/P11/P4/P8/P9/P10 接真）。
- PRD M2 出口（番茄钟全流程）真 HTTP 17/17 + 浏览器全流程实证；纪律 7 落地（TASK_TRANSITIONS/UNCLAIMABLE_STATUSES 生成到 contracts-ts，两侧共同消费）。
- 中文 FTS 结论回写契约 A §10.4（unicode61 不命中 CJK 子串，trigram 归 M3）。
- 收口后二轮实机 verify 35/35 + `/code-review high`（15 CONFIRMED）修复批：安全（activity done 归属门）、数据完整（tasks 分页聚合）、输入法（IME 误发）、时区（UTC 硬切统一 lib/time.ts）、实时性（channelFiles/usage 失效）等 15 项修复 + 契约 A v1.0.3（ActivityItemPublic.actor_member_id 派生字段）。
- 最终基线：后端 387 passed / 3 skipped，前端 vitest 18，双侧 typecheck/build/ruff 绿。
- 提交链：`42f20f0`（C0-C2）→ `ba73f72`（hardening）→ `6c12b90`（M2 后半）→ `cdb27db`（二轮 review）。

## 7. M3a 契约与校验（块 M3a 收口 2026-07-10）

来源：[M3-HANDOFF.md](M3-HANDOFF.md) · [M3-DEV-PLAN.md](M3-DEV-PLAN.md) · [M3A-EVIDENCE.md](../verify/M3A-EVIDENCE.md)

- 多 agent 工作流编排：地基 **E0（契约登记）∥ E1（建表迁移）并行** → 实现 **E2+E3（契约端点+T7）后端 ∥ B-M3-1（契约卡）前端并行** → 整合守门 → 实机 verify → `/code-review high`。文件域不相交，无冲突。
- **E 契约 v1.2 先行**：M3 契约面**零新 Agent 工具**（提交/force-start 人确认·C3 门，读走 get_task、起草走 request-draft 直投 + send_message 贴线程；画布结构编辑工具位随 M6）——engineering_docs/05 §3 裁决表。
- **L2 契约链路**：task_contracts 落库（0003_m3 三表一次建齐）+ TaskPlan/TaskHandoff/LoopContract body 模型（PRD §4.3 v1，M1 原缺）+ 提交/修订链（新 revision、旧行 superseded）+ request-draft S1 直投（`message.inject` + `contract_draft_request`）。
- **T7 流转门**：level=l2 置 in_review 校验活动 TaskHandoff deliverables/evidence 非空 → 422 HANDOFF_INCOMPLETE{missing}（人与 Agent 同拒）；deliverables/evidence 非空由 T7 执法（提交期允许空，可增量起草）。
- **升格 P-2**：PATCH /tasks/{id} 扩 level，仅 l1→l2 单向；l2→l1 拒 422 D1。
- **前端 P5 契约卡接真**：TaskPlan/TaskHandoff/revision/历史版本渲染 + 「让 @Agent 起草」入口 + T7 就地提示（交互 §5.4）；引入 happy-dom + testing-library 建首个组件渲染测试。
- **`/code-review high`（8 角度 finder）**：CONFIRMED 全修（6 正确性 + 3 质量 + 回归测试）：修订链竞态（分区唯一索引 `uq_task_contracts_active` + SAVEPOINT 重试）、loop_contract 挂 Task（TASK_CONTRACT_KINDS 门）、T7 经升格绕过（patch_task 补守护）、前端跨任务陈旧态（ThreadPanel key）、body 断言崩溃防御、T7 错误静默吞兜底、task_id 索引、占位文案收敛、契约 body 单测缺口。
- **收口基线**：后端 **421 passed / 3 skipped**（387→+34），前端 vitest **23**，双侧 typecheck/build、ruff、`pnpm gen` 两跑一致全绿。实机：真 HTTP 16/16 + 2 新守卫 + 浏览器契约卡/T7 就地提示截图。
- **块 M3b（画布与 gating）未开工**——按纪律不与 a 交错，另开会话按 M3-HANDOFF §5/§9b。

## 8. 挂账清理批（2026-07-10，M3a 与 M3b 之间的独立三批）

| 批 | 内容 | 提交 |
| --- | --- | --- |
| 批1 附件卡数据源 | 契约 A **v1.0.4**：`MessagePublic` 增读面派生 `files`（Public≠Row 第 5 例）；消息读面（列表/线程/响应/搜索）+ `message.created` 广播附着；0004 files 索引；前端 `m.files` 直消费删 `filesByMessage`。实机 9/9 + 截图（[B1-ATTACH-EVIDENCE.md](../verify/B1-ATTACH-EVIDENCE.md)） | `58b89b5` |
| 批2 keyset 分页 | `_pagination.keyset_page`（(created_at,id) 行值锚点 + LIMIT 下推）统一 messages/tasks/files/activity；messages before 修成紧邻回翻；ActivityScreen 'all' 单拉 + 客户端过滤、wsBridge 删多档 patch。实机 10/10 + 单请求截图（[B2-KEYSET-EVIDENCE.md](../verify/B2-KEYSET-EVIDENCE.md)） | `9331698` |
| 批3 pyright 清零 | 133 → **0**（`models.tbl()` Table 窄化 80×、`models.row_dict()` 非空窄化、daemon/api 零星标注）；pyright 并入根级 `pnpm typecheck` 守门 | 本批 |

收口基线：后端 **428 passed / 3 skipped**、web vitest 23、ruff / pyright / gen 确定 / 双侧 build 全绿。

## 已失效结论

| 历史表述 | 当前结论 |
|---|---|
| 项目只有文档与设计稿，没有产品代码 | M1/M2 产品实现、hardening 与二轮 review 修复批已完成 |
| 下一步从 A1 或 M1 实现开工 | M1/M2 全部收口，当前任务书 = M3-HANDOFF |
| M1/M2 契约或实现任务书是当前唯一入口 | 当前入口为 `CURRENT-HANDOFF.md`；开工入口 = `M3-HANDOFF.md` |
| `238`/`340`/`384`/`387 passed` 是最新测试基线 | 最新基线为 `421 passed, 3 skipped` + vitest 23（块 M3a 收口） |
| 前端仅依赖手工切换 API 基址或 mock `8642` | 已完成同源与代理路径 hardening，具体见修复报告 |
| pyright 有 109 个既有错误挂账 | 挂账批3 已清零（实清 133），pyright 已并入 `pnpm typecheck` 守门 |
| `421 passed` + vitest 23 是最新基线 | 挂账三批后为 `428 passed, 3 skipped` + vitest 23 |

## 当前接续任务

1. **块 M3a 已收口**；接续 = **块 M3b 画布与 gating**（M3-HANDOFF §5/§9b：E4 画布结构端点 → E5 blocked 推导+gating+force-start → E6 工程三角真机；B-M3-2 React Flow 画布 + B-M3-3 升格/force-start UI）。另开会话，不与 a 交错。
2. FTS trigram 浮动件（中文子串命中，A §10.4）——哪块顺手哪块收。
3. 在干净环境完成真实双 Agent OAuth 冷启动复验（可随 M3b E6 真机场景顺路）。
4. ~~分批清理 Pyright 既有错误~~ 已收（挂账批3，清零并入守门）。
5. ~~keyset 分页 + LIMIT 下推统一整改~~ 已收（挂账批2）。
