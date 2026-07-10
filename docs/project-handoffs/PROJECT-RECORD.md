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
- 提交链：`42f20f0`（C0-C2）→ `ba73f72`（hardening）→ `6c12b90`（M2 后半）→ 本修复批。

## 已失效结论

| 历史表述 | 当前结论 |
|---|---|
| 项目只有文档与设计稿，没有产品代码 | M1/M2 产品实现、hardening 与二轮 review 修复批已完成 |
| 下一步从 A1 或 M1 实现开工 | M1/M2 全部收口，当前任务书 = M3-HANDOFF |
| M1/M2 契约或实现任务书是当前唯一入口 | 当前入口为 `CURRENT-HANDOFF.md`；开工入口 = `M3-HANDOFF.md` |
| `238`/`340`/`384 passed` 是最新测试基线 | 最新基线为 `387 passed, 3 skipped` + vitest 18 |
| 前端仅依赖手工切换 API 基址或 mock `8642` | 已完成同源与代理路径 hardening，具体见修复报告 |

## 当前接续任务

1. 按 [M3-HANDOFF.md](M3-HANDOFF.md) 开工（E0 契约登记 + E1 建表；E 契约 v1.2 修订先行）。
2. 在干净环境完成真实双 Agent OAuth 冷启动复验（可随 M3 E6 真机场景顺路）。
3. 分批清理 Pyright 的 109 个既有错误（独立批）。
4. keyset 分页 + LIMIT 下推统一整改（M2 二轮 review 挂账，独立小批）。
