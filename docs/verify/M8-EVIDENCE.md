# M8 实机证据（L13 m8_verify + 浏览器可视 E2E）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-14，块 M8c L13 收口 |
| 范围 | M8c 引入的**新用户可见面**端到端真机验证：B-M8-3 外壳（新建频道 / 建 Agent）· L11 入职问候 · L1 原子建边（加固批）。O8 编排质量线（M8b）与 W9 双档 satisfied 的**正确性由单元套证**（见 §3）。 |
| 脚本 | `scratchpad/m8_verify.py`（复用 `m6a_harness` 范式：真 uvicorn 子进程 + 真 daemon-sim[真 websockets + FakeAdapter] + 隔离临时库 + taskkill 杀树；`--keep` 保活接浏览器） |
| 结果 | **`scratchpad` 探针 10/10 ALL PASS**（[M8-VERIFY-results.json](M8-VERIFY-results.json)）+ 浏览器可视 E2E DOM 核实全过（§2）+ 零 console 错误 |

## 1. m8_verify 探针 10/10 ALL PASS（真 uvicorn + 真 daemon-sim）

| 探针 | 断言 | 结果 |
| --- | --- | --- |
| P0 | daemon-sim 真 websockets 连上真 server | PASS |
| **S1** | POST /channels 新建频道进快照（侧栏「新建频道」真实端点，L10 零新端点） | PASS |
| **S2** | POST /agents 建 Agent 进成员表（Members 页「创建 Agent」真实端点） | PASS |
| **L11-0** | seed 工作区默认 `onboarding_greeting=False`（裁决 #9 默认关，改 models.py server_default → 0001 metadata.create_all 建表即默认关） | PASS |
| **L11-1** | 默认关 → 上线零问候、不落幂等标记 | PASS |
| **L11-2** | 开关开 → 上线后 daemon **线级收到 InjectKind.SYSTEM 入职问候一条**（body = `[system → 仅你可见] 欢迎加入这个工作区！请到 #all …`，经 `FakeAdapter.injects` 观测） | PASS |
| **L11-3** | diagnostic 幂等标记 `agent.onboarding_greeting` 落一条 | PASS |
| **L11-4** | 重启（再次 START）不重复问候（标记 airtight，inject 计数 1→1） | PASS |
| **L1-1** | 带上游 merge 节点：节点 + 全部入边同事务原子落地（`system_status=idle`、入边存在，**非空成功**，封 K1 竞态） | PASS |
| **L1-2** | 悬空上游 → 422（全量收集，不留悬挂节点） | PASS |

> L11 于**线级**观测（daemon 经真 websockets 收到 MESSAGE_INJECT 帧，`FakeAdapter.injects` 记录 body）——证明「lifecycle START 成功 → tx.after_commit 提交后直投问候 → 帧真达 daemon」全链通，且默认关/重启不重复的护栏在真机成立。

## 2. 浏览器可视 E2E（同源 build SPA + `--keep` 真机；DOM 核实）

> 本机截图工具超时（既有已知，B-M8-2 同）——用 `get_page_text` / `javascript_tool` DOM 核实（同 B-M8-2 体例）。零 console 错误。

- **B-M8-3 ① Members「创建 Agent」**：Members 页渲染「创建 Agent」按钮 → 点击 → `[role=dialog][aria-label=创建 Agent]` 打开（title「创建 Agent」、角色模板 select 在位、字段 = 名字/模型/所在机器/角色模板/成员说明）。
- **B-M8-3 ③ 侧栏「新建频道」**：`.newch` 已 `role=button`（原死壳）→ 点击 → `[role=dialog][aria-label=新建频道]` 打开（title「新建频道」、字段 = 名字/说明（可选）/私有频道、private toggle 在位）。
- **外壳端点反流 UI**：harness 经 POST /channels 建的 `m8-shell` 频道现身侧栏频道列表（`delivery, conflict, m8-shell`）；经 POST /agents 建的 `ShellBot` 现身 Members AGENTS 段——**建频道/建 Agent 端点 → UI 实时可见**全链通。
- console：`onlyErrors` 查询 **无错误**。

## 3. O8 编排质量线（M8b）与 W9：正确性覆盖归属

按 [M8-HANDOFF](../project-handoffs/M8-HANDOFF.md) 防返工锚点 7「**O8 红例用 daemon-sim / 单测，真 CLI 只进 L12/L13 演示位**」与 §9b #11「O8 真机全链**可并入 L13**」——O8 协调护栏（8 轮触顶 / stall 3 次 / 重复 delta 加倍 / replan 403 rule=O8 / 阻断 blocked_at / 恢复归零 / F6 崩溃续算 / CAS 竞态）与 W9 双档 satisfied 的**正确性由单元套逐条红例证**，全量 **pytest 1122 passed / 4 skipped** 跑在**真 ORM + 真 SQLite**（`alembic upgrade head` 临时库）上：

- `test_summary.py`（15+）：CAS 三计数 / scan 幂等 / gating 抑制 / F6 跨事务续算显式红例
- `test_delta.py`：replan 第 2 次 403 rule=O8 / 首次消费 / 重复 delta 加倍
- `test_gating.py` + kernel golden 双跑：W9 partial 放行 / strict 全量回归逐字节

不在 deterministic harness 内烧真 Orchestrator CLI 空转多轮（成本高、非确定）——多节点真机拆解演示归 L12 教程位（`使用教程/`）。

## 4. 收口守门（全绿）

| 门 | 值 |
| --- | --- |
| `uv run pytest -q` | **1122 passed / 4 skipped**（M8c c-1 起点 1117 → +5 L11） |
| `pnpm -F @coagentia/web test` | **512**（起点 502 → +10 B-M8-3） |
| `pnpm typecheck`（pyright + 双 tsc） | **0** |
| `uv run ruff check .` | 净 |
| `pnpm gen` 后 diff | 空（`rest.ts` onboarding_greeting `@default false` 一处，确定） |
| `pnpm -F @coagentia/web build` | 绿 |
| m8_verify 探针 | **10/10 ALL PASS** |
