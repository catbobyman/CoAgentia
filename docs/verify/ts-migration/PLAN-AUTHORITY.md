# TypeScript 迁移计划权威声明

> 生效日期：2026-07-20
> 批准人：owner
> 当前阶段：P0（进行中）

## [根 plan.md](../../../plan.md) 的唯一执行权威地位

仓库根 [`plan.md`](../../../plan.md) v1.0 是 CoAgentia 全量 TypeScript 迁移的唯一执行权威。它决定终态、范围、依赖 DAG、验收门、评审协议和不可逆操作的 owner gate。

## 历史/参考文档

| 文档 | 状态 | 允许用途 |
|---|---|---|
| `docs/project-handoffs/TS-MIGRATION-ROADMAP.md` | `SUPERSEDED/HISTORICAL` | daemon-ts 迁移当时的状态与旧终态决策 |
| `docs/project-handoffs/TS-DEV-PLAN.md` | `SUPERSEDED/HISTORICAL` | server 摸底数据、旧波次和技术候选 |
| `docs/project-handoffs/CURRENT-HANDOFF.md` | 运行快照 | 当前 Git/守门/挂账事实；不得覆盖迁移范围或依赖 |
| `docs/project-handoffs/*M*-*.md` 与 `archive/` | 历史 | 已完成里程碑的事实、教训与证据 |

## 冲突规则

1. 若历史文档与根计划在终态、命令、依赖或阶段状态上冲突，以根计划为准。
2. 产品行为仍以 A/B/C/D/E/E2 六套工程契约为准；语言迁移不授权暗改契约。
3. 本声明不授权 push、发布 npm 包、删除 Python 实现、修改生产数据库或其他不可逆操作。
4. 权威扫描应要求本文与两份旧计划的状态标记存在，并拒绝第二份 active plan。
