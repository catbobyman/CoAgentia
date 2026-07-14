# 项目交接文档索引

本目录集中保存 CoAgentia 的交接状态与阶段记录，避免工作区根目录存在多份互相冲突的 `HANDOFF`。

| 文档 | 定位 | 维护规则 |
|---|---|---|
| [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) | 当前唯一有效的交接入口 | 每次阶段收口时更新 |
| [M8-HANDOFF.md](M8-HANDOFF.md) | **M8 任务书（当前进行中）**：加固批 + O8 编排质量线 + 单机产品化外壳 + 体验收官（2026-07-14 立项；O8 实现权威 = orchestrator_docs/Orchestrator汇总设计.md） | 里程碑推进期维护，收口后移入 archive/ |
| [M8-DEV-PLAN.md](M8-DEV-PLAN.md) | M8 逐模块执行计划与进度表 | 随模块完成更新 |
| [M8-SCOPE-DRAFT.md](M8-SCOPE-DRAFT.md) | M8 立项草案（**已废止**，由 M8-HANDOFF 取代；立项过程存档） | 只读 |
| [M7-DEV-PLAN.md](M7-DEV-PLAN.md) | M7 逐模块执行计划与进度表（**已收口，只读**；任务书 = archive/M7-HANDOFF.md） | 阶段记录，收口后保留只读 |
| [M6-DEV-PLAN.md](M6-DEV-PLAN.md) | M6 逐模块执行计划与进度表（**已收口，只读**；任务书 = archive/M6-HANDOFF.md） | 阶段记录，收口后保留只读 |
| [CODEX-CONTEXT.md](CODEX-CONTEXT.md) | **接手任务的 AI 工程师完整项目上下文**（自足：术语解码/架构/纪律/坑清单；Codex CLI / Claude Code 会话通用）；仓库根 `AGENTS.md` 为其精炼版工作规程（Codex CLI 自动加载） | 大版本状态变化时更新时点注记 |
| [COLLAB-MODEL.md](COLLAB-MODEL.md) | **M6 多模型协作模式 v2「Fable 单窗编排」**（2026-07-12；M6 已按此收口）；M7 立项复审 | 阶段推进时核对;M7 立项复审 |
| [M2-DEV-PLAN.md](M2-DEV-PLAN.md) / [M3-DEV-PLAN.md](M3-DEV-PLAN.md) / [M4-DEV-PLAN.md](M4-DEV-PLAN.md) / [M5-DEV-PLAN.md](M5-DEV-PLAN.md) | 各里程碑逐模块执行计划与进度表（均收口） | 阶段记录，收口后保留只读 |
| [PROJECT-RECORD.md](PROJECT-RECORD.md) | 历史阶段、关键结果与过时结论汇总 | 阶段完成后追加，不覆盖历史 |
| [archive/](archive/) | 原始任务书快照（M1–M7 任务书收口后均移入） | 只读归档，不再维护状态 |

> **M1–M7 里程碑任务书均已收口并移入 `archive/`**（M7-HANDOFF 2026-07-13 收口移入）。里程碑级最新状态见 `CURRENT-HANDOFF.md`，阶段结论见 `PROJECT-RECORD.md`。**MVP 全里程碑 M1–M7 完成 = PRD 全部规划里程碑收口（M7 = PRD M7 出口，实机 29/29 + 浏览器 E2E）；无后续 PRD 里程碑，后续 = M8+ 挂账消费（M7-RESERVATION-AUDIT.md R-1~R-15）未立项**。

## 原文件迁移表

| 原位置 | 新位置 | 状态 |
|---|---|---|
| 工作区根目录 `HANDOFF.md` | [archive/HANDOFF.md](archive/HANDOFF.md) | 已过时，设计与品牌阶段快照 |
| 工作区根目录 `M1-HANDOFF.md` | [archive/M1-HANDOFF.md](archive/M1-HANDOFF.md) | 已完成，M1 契约阶段任务书 |
| 工作区根目录 `M1-IMPL-HANDOFF.md` | [archive/M1-IMPL-HANDOFF.md](archive/M1-IMPL-HANDOFF.md) | 已完成，M1 实现阶段任务书 |
| 工作区根目录 `SESSION-HANDOFF.md` | [archive/SESSION-HANDOFF.md](archive/SESSION-HANDOFF.md) | 已被当前交接取代 |
| `docs/m1-review-fixes-20260709/HANDOFF.md` | [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) | 当前有效 |
| `M2-HANDOFF.md` | [archive/M2-HANDOFF.md](archive/M2-HANDOFF.md) | 已完成，M2 任务与看板任务书 |
| `M3-HANDOFF.md` | [archive/M3-HANDOFF.md](archive/M3-HANDOFF.md) | 已完成，M3 契约与画布任务书（§9a+§9b 全绿 = PRD M3 出口） |
| `M4-HANDOFF.md` | [archive/M4-HANDOFF.md](archive/M4-HANDOFF.md) | 已完成，M4 护栏与提醒任务书（= PRD M4 出口） |
| `M5-HANDOFF.md` | [archive/M5-HANDOFF.md](archive/M5-HANDOFF.md) | 已完成，M5 模板与第二 runtime 任务书（= PRD M5 出口） |
| `M6-HANDOFF.md` | [archive/M6-HANDOFF.md](archive/M6-HANDOFF.md) | 已完成，M6 Orchestrator 与 Project 任务书（= PRD M6 出口，实机 verify 48/48） |
| `M7-HANDOFF.md` | [archive/M7-HANDOFF.md](archive/M7-HANDOFF.md) | 已完成，M7 预览·部署·打磨任务书（= PRD M7 出口 = MVP 收口，实机 29/29 + 浏览器 E2E） |

## 维护约定

1. 新会话先读 `CURRENT-HANDOFF.md`，需要历史背景时再读 `PROJECT-RECORD.md`。
2. 当前状态、测试数字和下一步只在 `CURRENT-HANDOFF.md` 保持权威版本。
3. 阶段结束后把结论写入 `PROJECT-RECORD.md`；原始任务书移入 `archive/`，不再原地更新。
4. 新增交接文件必须先更新本索引，避免重新出现多个“当前”入口。
