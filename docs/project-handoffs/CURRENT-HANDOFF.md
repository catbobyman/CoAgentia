# CoAgentia 当前交接（M3 收口态）

| 项 | 内容 |
| --- | --- |
| 更新 | 2026-07-10，块 M3b「画布与 gating」收口并提交后重写（历次增补已沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md) §1–§9，本文只保留当前态） |
| 定位 | **当前唯一有效的交接入口**（README 约定 1/2）：新会话先读本文；历史背景读 PROJECT-RECORD；里程碑任务书均在 [archive/](archive/) |
| 一句话状态 | **M1/M2/M3 三个里程碑全部收口**（PRD M3 出口「工程三角上画布跑通 + blocked gating 生效」已实机达成）；**接续 = M4 护栏与提醒（尚未立项）** |

## 1. 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia`（monorepo：apps/server·web·daemon·mock-server + packages/contracts·contracts-ts·fixtures）；**无 git remote，全部提交仅存本地** |
| 分支 / HEAD | `main` / `080ed44 M3b complete: canvas & gating`；工作树干净 |
| 提交链（近） | `d5f092e` M3a → `58b89b5`/`9331698`/`0b61669` 挂账三批 → `85f8568` M3B-KICKOFF → `080ed44` M3b 收口 |
| 测试基线 | 后端 **483 passed / 3 skipped**（`uv run pytest -q`）· web vitest **76** · pyright **0**（并入 `pnpm typecheck`）· ruff 干净 · `pnpm gen` 确定（两跑 diff 空）· 双侧 build 绿 |
| 契约版本 | A **v1.0.4**（+§10.4 trigram 收口结论）· B v1.1 · C · D · E **v1.2**（M3 契约面零新 Agent 工具）；事实源 = `D:\Project4work\Agenthub_7_8\engineering_docs\` 五契约 + `docx_agenthub\CoAgentia-PRD.md` |
| 建表批次 | 0001 M1（17 表）→ 0002 M2（tasks/task_events/message_task_refs/activity_items + messages_fts）→ 0003 M3（task_contracts/canvas_nodes/canvas_edges）→ 0004 files 索引 → **0005 messages_fts 改 trigram**；`held_drafts` **尚未建**（M4 建表批次） |
| 实机证据 | [M3B-EVIDENCE.md](../verify/M3B-EVIDENCE.md)（17/17 + 6 截图 + console 0 错误）；此前各批证据同目录 |

## 2. 里程碑总览（详情 = PROJECT-RECORD 对应节）

| 里程碑 | 出口 | 收口 |
| --- | --- | --- |
| M1 契约+实现+hardening | 真实两 Agent 对话/文件产出/reminder | `f2c993f` 合 main（§2–§5） |
| M2 任务与看板 | 番茄钟全流程（人发任务→Agent 认领交付→done） | `6c12b90`+`cdb27db`（§6） |
| M3a 契约与校验 | L2 契约链路（提交/修订链/request-draft/T7 门/升格） | `d5f092e`（§7） |
| 挂账三批 | 附件卡数据源 / keyset 分页 / pyright 清零 | `58b89b5`/`9331698`/`0b61669`（§8） |
| **M3b 画布与 gating** | **PRD M3 出口**：画布建图/成环拒/blocked 推导+投递 gating/force-start/React Flow/FTS trigram | **`080ed44`（§9）** |

## 3. 系统当前能力面（一览）

- **IM 基座**（M1）：频道/DM/线程/@mention/文件/已读；真 daemon（Claude Code 适配器）双 Agent 对话；WS 事件驱动无刷新。
- **任务域**（M2）：as_task/convert 建任务、claim/assign/状态机（TASK_TRANSITIONS 单一事实源）、看板 P3/P11、搜索三分组、Activity、daemon MCP 六工具、usage 归属。
- **L2 契约**（M3a）：TaskPlan/TaskHandoff 提交与修订链、Agent 起草 request-draft S1 直投、T7 流转门（l2→in_review 校验 handoff）、升格 PATCH level l1→l2。
- **编排画布**（M3b）：每频道画布页签（React Flow）——节点=任务（agent 节点=第三创建途径，建 L2+锚点消息）、边=依赖（写事务拓扑排序防环）、基线快照指纹推进；**blocked 实时推导**（`kernel/graph.py` 权威 + 前端 `lib/graph.ts` 镜像，`golden/graph.json` 双跑对照）；**投递层 gating**（blocked 任务线程消息不唤醒、不入投递批、read_position 水位不越过）；**force-start**（仅人类、双留痕、不改状态、本次放行）；看板 blocked 徽标。
- **中文检索**（浮动件）：messages_fts trigram（≥3 字 MATCH + <3 字 LIKE 兜底，元字符转义）。

## 4. 接续 = M4 护栏与提醒（尚未立项）

**PRD §8 M4 行**：freshness check + HeldDraft 卡片 + 三键干预 + 超时自愈 + 升级（G1–G6）；沉默提醒升级链（D5）。**出口验收**：制造一次 held 场景（卡片可见、放行 1 分钟内交付）+ 制造一次沉默任务（提醒与升级触达）。

开工建议（体例沿用 M2/M3 先例）：

1. **建 M4-HANDOFF 任务书**（范围/模块分解/DoD/出口清单，两块竖切评估）+ 契约修订核对先行（纪律 1：D 契约 freshness/held 触发器语义、A 契约 held_drafts 表批次、E 契约 Agent 工具位增减——修订完成后才动代码）。
2. **M4 开工第一步 = 既有挂账**：`_emit_activity` 从 [routes/messages.py:117](../../apps/server/src/coagentia_server/routes/messages.py) 迁 service 层——M4 升级类 activity 走 hub/reminder 后台路径，无法合理 import 路由层私有函数（M2 二轮 review 挂账，已两次顺延，勿再跳过）。
3. **可复用资产**（已就位勿重建）：`HeldDraftRow`/`HeldDraftStatus`/`HeldReasons` 契约形状已冻结（entities.py，表未建）；WS `held_draft.created/updated` + Envelope 已登记；`reminders` 表+扫描循环 M1 在线（hub `run_reminder_scan`）；`LoopContractBody` 模型 M3a 已建齐（生成归 M4）；投递 gating/唤醒判定点已在 hub 两处收口（freshness 门大概率挂同一层）；`tasks.silence_override_h` 列 M2 已建。

## 5. 挂账清单（非阻塞，勿当漏项重新发明）

| 项 | 说明 | 归属 |
| --- | --- | --- |
| `_emit_activity` 迁 service 层 | 见上，**M4 开工第一步** | M4 |
| `patch_task` 无法清空 `silence_override_h` | `if v is not None` 丢 null，无法重置任务级覆盖回 NULL；该列 M4 才消费，顺手修 | M4 顺手 |
| 性能小批 | hub `usage.batch` 逐事件 SELECT（可批内 IN 预查）；search 双 MATCH+LIKE 扫描 | 独立小批 |
| `task #n` refs 无 UI 消费面 | refs 落库但引用消息不渲染任务 chip | 顺手评估 |
| P11/P3 看板双实现抽 `<TaskBoard>` | blocked 徽标已同构两份，抽共享组件可收 | 顺手评估 |
| messages_fts 键于 messages.rowid | VACUUM 会失同步（external-content 结构性约束，trigram 重建未改变） | 观察项 |
| OAuth 冷启动复验 | M1 遗留；M3b 用真 websockets daemon-sim 复证网关侧，真双 Agent OAuth refresh 竞争仍依赖既有确定性单测 | 择机 |

## 6. 启动方式

**真实开发**：终端 1 `uv run coagentia-server`（8787）；终端 2 `pnpm --filter @coagentia/web dev`（5173，代理 /api→8787）。
**同源构建**：`pnpm --filter @coagentia/web build` 后 `uv run coagentia-server`，开 `http://127.0.0.1:8787`（自动发现 apps/web/dist；异地部署设 `COAGENTIA_WEB_DIST`）。
**Mock**（显式开启才用）：`VITE_API_BASE=http://127.0.0.1:8642` + `VITE_MOCK_MODE=true` + mock-server。
**隔离实机 verify 范式**：临时库 `COAGENTIA_ALEMBIC_URL` alembic head + seed + 注入测试 key + 独立端口（8799 先例）；参照 M3B-EVIDENCE 与 scratchpad launcher 脚本体例。

## 7. 守门命令（全绿才算收口）

```
uv run pytest -q                    # 483 passed / 3 skipped 基线，零回归
pnpm -F @coagentia/web test         # vitest 76 基线，只增不减
pnpm typecheck                      # 含 pyright（0 错，新债即红）+ 双 tsc
uv run ruff check .
pnpm gen                            # 后 git diff 应为空（生成物确定性）
pnpm -F @coagentia/web build
```

## 8. 注意事项

- **无 git remote**：所有提交仅在本地 main，如需备份/协作须先 `git remote add` 并 push（可选项，owner 决定）。
- **环境要求**：SQLite ≥ 3.35（RETURNING）；真 claude CLI 踩坑见记忆/PROJECT-RECORD（stream-json `--verbose` 必需、须排空 stderr 等）。
- **迁移纪律**：新迁移按批次显式点名建表（勿 metadata.create_all 全集——坑1）；给既有表加索引/约束须 `if_not_exists`。
- **纪律 8（图算法单源）**：改动无环/blocked 语义必须同步 `kernel/graph.py` + `lib/graph.ts` + `golden/graph.json` 三处，两侧靠同一判例集守门。
- **gating 语义要点**（M3b code-review 教训）：「gating 作用于投递层」= 唤醒触发 **和** 投递批双面——held 消息须从投递批剔除且 read_position 水位不越过它，否则被兄弟消息顺带消费；M4 freshness/held 若挂同一投递层，沿用 `_filter_gated` 范式。
- 实机验证起的 server/浏览器/daemon-sim 进程结束前应关闭（8799 等端口）。
