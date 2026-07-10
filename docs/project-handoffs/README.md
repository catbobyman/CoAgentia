# 项目交接文档索引

本目录集中保存 CoAgentia 的交接状态与阶段记录，避免工作区根目录存在多份互相冲突的 `HANDOFF`。

| 文档 | 定位 | 维护规则 |
|---|---|---|
| [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) | 当前唯一有效的交接入口 | 每次阶段收口时更新 |
| [M2-HANDOFF.md](M2-HANDOFF.md) | **M2 里程碑任务书**（进行中）：任务与看板的范围、模块分解、DoD、出口清单 | M2 期间随模块勾选；收口后移入 archive/ |
| [PROJECT-RECORD.md](PROJECT-RECORD.md) | 历史阶段、关键结果与过时结论汇总 | 阶段完成后追加，不覆盖历史 |
| [archive/](archive/) | 原始交接文档快照 | 只读归档，不再维护状态 |

## 原文件迁移表

| 原位置 | 新位置 | 状态 |
|---|---|---|
| 工作区根目录 `HANDOFF.md` | [archive/HANDOFF.md](archive/HANDOFF.md) | 已过时，设计与品牌阶段快照 |
| 工作区根目录 `M1-HANDOFF.md` | [archive/M1-HANDOFF.md](archive/M1-HANDOFF.md) | 已完成，M1 契约阶段任务书 |
| 工作区根目录 `M1-IMPL-HANDOFF.md` | [archive/M1-IMPL-HANDOFF.md](archive/M1-IMPL-HANDOFF.md) | 已完成，M1 实现阶段任务书 |
| 工作区根目录 `SESSION-HANDOFF.md` | [archive/SESSION-HANDOFF.md](archive/SESSION-HANDOFF.md) | 已被当前交接取代 |
| `docs/m1-review-fixes-20260709/HANDOFF.md` | [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) | 当前有效 |

## 维护约定

1. 新会话先读 `CURRENT-HANDOFF.md`，需要历史背景时再读 `PROJECT-RECORD.md`。
2. 当前状态、测试数字和下一步只在 `CURRENT-HANDOFF.md` 保持权威版本。
3. 阶段结束后把结论写入 `PROJECT-RECORD.md`；原始任务书移入 `archive/`，不再原地更新。
4. 新增交接文件必须先更新本索引，避免重新出现多个“当前”入口。
