# M3b 开工交接（画布与 gating）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-10，挂账清理三批收口后（M3a 与挂账批均已提交，main 干净） |
| 用途 | **块 M3b 开工会话的唯一入口**：现状快照 + 已就位资产 + 缺口清单 + 推进顺序。任务书条文以 [M3-HANDOFF.md](archive/M3-HANDOFF.md) §4/§5/§7/§9b 为准，本文不复制只收敛 |
| 出口 | §9b 清单 7–13 全绿 = **PRD M3 出口达成**；届时 M3-HANDOFF 移入 archive/（README 约定 3） |

## 1. 现状快照（开工前提，勿重查）

- 提交链：`d5f092e`（M3a 收口）→ `58b89b5`（挂账批1 附件卡）→ `9331698`（批2 keyset）→ `0b61669`（批3 pyright 清零）。分支 `main`，工作树干净。
- 基线：后端 **428 passed / 3 skipped**、web vitest **23**、ruff / **pyright 0**（已并入 `pnpm typecheck`）/ `pnpm gen` 确定性全绿。
- 契约版本：A **v1.0.4**（MessagePublic.files 读面派生）、B v1.1、D、E **v1.2**（M3 契约面零新 Agent 工具）。
- 块 M3a 交付已在线：契约提交/修订链、request-draft S1 直投、T7 门（l2→in_review）、升格 PATCH level l1→l2、P5 契约卡接真。证据 [M3A-EVIDENCE.md](../verify/M3A-EVIDENCE.md)。

## 2. 范围（= M3-HANDOFF §9b，7 项）

后端 **E4**（画布结构端点）→ **E5**（blocked 推导 + 投递 gating + force-start）→ **E6**（工程三角六节点端到端实机 = 收口）；前端 **B-M3-2**（P2 画布页签 React Flow）+ **B-M3-3**（升格/force-start UI + 看板 blocked 徽标）；浮动件 **FTS trigram**（哪步顺手哪步收，明确移出须 owner 拍板记录）。非目标：多画布（M3 沿用每频道一行，裁决 9）、Agent 画布工具位（裁决 7，M6 提案流后再议）、O9 结构变更管控。

## 3. 已就位资产（拿来即用，勿重建——2026-07-10 逐项核实）

| 资产 | 位置 | 状态 |
| --- | --- | --- |
| `canvases` 表 + 行 | M1 预留 #2，每频道一行已随建频道落库 | `CanvasRow{baseline_version, baseline_hash}` 基线字段就位（契约 A §6） |
| `canvas_nodes` / `canvas_edges` 表 | `0003_m3` 已建，**空置** | CHECK（agent→task_id / system→system_action）、UNIQUE(canvas_id,from,to)、task_id UNIQUE 均已落 |
| 实体模型 | `entities.py:402-448` | CanvasRow/NodeRow/EdgeRow + Public 已冻结；`CanvasNodeKind`/`SystemAction`/`SystemNodeStatus` 枚举已有 |
| 端点登记 | `rest.py:511` `ENDPOINTS_M3`（12 条） | 画布组 8 条 + force-start **已登记但未 serve**——E4/E5 落地时补「目录 vs 实 serve」一致性测试（M2 C4 先例） |
| WS 事件 | `ws.py:79-85` | `canvas.node_added/node_updated/node_removed/edge_added/edge_removed/layout_updated/baseline_advanced` 7 事件 + 事件 data 模型已预登记 |
| 错误码 | `rest.py` | `GRAPH_CYCLE`(422)、`DAEMON_OFFLINE`(503) 已有，**无需增码** |
| force-start 留痕载体 | `enums.py:89` | `TaskEventKind.FORCE_START` 已有 |
| 建任务范式 | `tasks_service.create_task` + `emit_task_created` | E4「agent 节点=第三创建途径」直接复用（as_task/convert 先例） |
| 升格/T7 | M3a 已在线 | B-M3-3 升格弹层直接调 PATCH level；T7 拒绝已有前端就地提示范式（ThreadPanel） |
| 唤醒/投递层 | `computers/hub.py`（backlog/deliver/wake） | E5 gating 挂在**投递判定处**，不限制状态写（R4/R7）；hub 已有 sync 桥范式（send_lifecycle/inject） |
| keyset 分页 | `routes/_pagination.keyset_page`（挂账批2 新增） | 画布快照如需列表面直接复用 |
| 附件读面 | 契约 A v1.0.4 `MessagePublic.files` | E4 锚点系统消息广播自带（无需处理附件） |
| 设计稿 | `docx_agenthub/04-设计稿/afterglow-ds/previews/` P2a-d 四稿 | B-M3-2 playwright 1440×900 对照消费，token 零发明 |
| 前端测试基座 | happy-dom + testing-library（M3a 引入） | B-M3-2/3 行为测试直接用 |

## 4. 缺口清单（M3b 要新建的）

1. **契约登记补齐（开工第一步，E0b）**：`rest.py` 无画布请求/响应模型——需建 `CanvasSnapshot`（canvas + nodes + edges）、NodeCreate/NodePatch、EdgeCreate、LayoutPut、force-start 响应等，按 B §4.9 条文；mock-server **无任何 canvas 端点形状**（纪律 4：mock 是形状源，须补读端点空形状）；`pnpm gen` 重生成入库。
2. **E4 画布结构端点**：快照 / nodes CRUD（agent 节点同步建 L2 任务 + 系统代发锚点消息；DELETE 解除引用不删任务 C8）/ edges CRUD（写事务内拓扑排序，成环 GRAPH_CYCLE）/ layout PUT（不 bump 基线）；每画布**串行化点** + baseline bump + `canvas.baseline_advanced` 广播（规范快照指纹确定性测试）。
3. **E5**：blocked = 边表 + 上游任务状态**实时推导不落库**（裁决 2）；gating 只作用投递层（blocked 不唤醒不投递，状态写不受限）；`POST /tasks/{id}/force-start` 仅人类（Agent 403 rule=C3）、写 task_events(force_start) + 任务线程系统消息、解除该节点本次 gating（裁决 3：不改状态不删边）。
4. **B-M3-2**：**react-flow 未安装**（apps/web 无依赖）——引入 React Flow；节点=任务卡实时着色（task.updated + canvas.* 双驱动）、拖拽建节点（=建 L2 任务弹层）/连断边（前端 TS 成环预判红色反馈 + 服务端权威复核）、layout 防抖 PUT、节点深链 ?task= 双向。**纪律 8**：无环/blocked 的前后端算法用同一组黄金用例对照（防 P11/P3 双实现漂移复发）。
5. **B-M3-3**：L1 拖入画布 → 升格补契约弹层（Agent 起草 request-draft / 手填两路）；blocked 节点 force-start 按钮（二次确认 + 留痕提示，仅人类可见）；P3/P11 看板 blocked 徽标——摸到时顺手评估抽 `<TaskBoard>`（挂账）。
6. **E6 收口**：工程三角六节点 DAG 真机脚本（框定→评审门→实现契约→TDD 实现→独立验收→人类终审）：blocked 标注与不唤醒 → 逐节点推进含 T7 门实测 → force-start 一次留痕 → 全程 WS 无刷新；录屏+截图归档 `docs/verify/`。**OAuth 冷启动复验可顺路**（M1 遗留，结论单独记录）。
7. **浮动件 FTS trigram**：messages_fts 虚表重建迁移（tokenize=trigram）+ 回填；中文子串命中实测回写契约 A §10.4；`GET /search` 形状不变。

## 5. 裁决速查（已拍板勿再问，全文见 M3-HANDOFF §7）

裁决 2 blocked 不落库 · 裁决 3 force-start=解除本次投递 gating+双留痕（不改状态不删边）· 裁决 4 T7 只对 l2 · 裁决 7 画布端点全员可用但 Agent 工具位不开放 · 裁决 8 删节点确认文案是 UI 责任 · 裁决 9 不做多画布。**迁移注意（挂账批1 新教训，坑1 索引变体）**：给既有表加索引/约束的迁移必须 `if_not_exists`——0001 create_all 按实时 metadata 会连带建出新加的 `__table_args__`。

## 6. 推进顺序与会话切分建议

```
E0b 契约登记 + mock 形状（地基，先行）
  → E4 画布结构端点 ──┐            ┌─ B-M3-2 画布页签（E0b 后即可吃 mock 形状）
  → E5 gating/force-start ─┴─ 整合守门 ─┴─ B-M3-3 升格/force-start UI
  → E6 工程三角实机（主 loop 亲为）→ /code-review high → 三处文档同步
```

- E4→E5 串行（gating 依赖边表）；前端两模块与后端文件域不相交可并行（M3a 编排先例）。
- 若走多 agent 工作流：文件域切分参照 M3-DEV-PLAN §3 体例；建议同批建 **M3-DEV-PLAN §7（块 M3b 进度表）** 逐模块打点（纪律 5）。
- 守门一览：`uv run pytest -q`（428 基线零回归）+ `pnpm -F @coagentia/web test`（23 基线）+ `pnpm typecheck`（**含 pyright，新债即红**）+ `uv run ruff check .` + `pnpm gen` diff 空 + `pnpm -F @coagentia/web build`。

## 7. 挂账联动（勿当漏项重新发明）

- `task #n` refs 无 UI 消费面 → 画布/深链批**顺手评估**（refs → 消息内迷你 chip）。
- P11/P3 看板双实现抽 `<TaskBoard>` → B-M3-3 摸到 blocked 徽标时顺手评估。
- 性能小批（hub usage.batch 逐 SELECT / search 双 MATCH+LIKE）→ 不阻塞 M3b，独立小批。
- `_emit_activity` 迁 service 层 → **M4 开工第一步**，M3b 勿动。

## 8. 启动方式

真实开发模式 / 同源构建模式同 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) §启动方式（8787 + 5173）。实机 verify 沿用隔离 launcher 范式（临时库 alembic head + seed + 独立端口，M3A/B1/B2 证据先例）。
