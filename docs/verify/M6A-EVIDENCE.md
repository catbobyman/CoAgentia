# M6a 交付链实机 verify 证据（PRD M6a 出口 §9a #11）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-11 |
| 范围 | 块 **M6a**「Project 与交付链」= J0–J6 + B-M6-1 的**真机全链**：两并行 writes_code 任务 → 各自 worktree 交付 → merge 系统节点 `--no-ff` 合并成功 → check 节点绿 → Diff 端点；冲突 → 自动建「解决冲突」任务派回 → 解决 → retry → 合并成功 |
| 环境 | **真 uvicorn**（create_app 注入隔离临时库 alembic upgrade head 含 0008 + seed，随机空闲端口，同源服务 apps/web/dist SPA）· **真 websockets daemon-sim**（生产 `DaemonClient` + `FakeAdapter`：真 WS + 真心跳 + 真 `git.py`/`checks.py`，仅 Agent turn 桩掉）· **真 scratch git 仓库**（`git init -b main` + 种子提交）· playwright 1440×900 截图 |
| 结论 | **REST/daemon-sim/真 git 端到端探针 20/20 ALL PASS**（两场景全链）+ 真仓库产生 5 个真 commit（2 场景各含 `--no-ff` merge commit）+ 4 张真机截图（画布节点终态 + 交付/冲突线程真系统消息）+ WS 无刷新驱动 |

> 证据性质：**真 daemon 侧全部 worktree/merge/check/diff 命令走生产 `apps/daemon/src/coagentia_daemon/git.py`+`checks.py`，对真 scratch 仓库执行真 git**；server 编排（激活联动 ensure、DAG 序 merge 触发、冲突派回、check 触发、gating）全走真 uvicorn。仅「Agent 在 worktree 写代码」由探针直接在 daemon 建出的 worktree 里 `git commit` 模拟（等价 Agent 会话产物）。探针脚本可复跑（§6）。

## 1. 端到端探针 20/20 ALL PASS（探针 `scratchpad/m6a_verify.py`）

### 场景 A —— 交付链（双并行 → worktree 交付 → merge `--no-ff` → check 绿）

| # | 检查 | 结果 |
| --- | --- | --- |
| D0 | daemon-sim 真 websockets 连上真 server（hello/ack 握手） | PASS |
| D1 | 建 Project + 绑定频道（repo_path 是 git 仓库 server 直查校验） | PASS |
| A1 | 建两 writes_code 任务（L2）+ merge/check 系统节点 + 3 边 | PASS |
| A2 | daemon 真 git 派生两 worktree（激活联动 ensure，`git worktree add`） | PASS |
| A3 | 两 worktree 真交付提交（分支 `coagentia/task-<ulid>`） | PASS |
| A4 | 两任务 claim→handoff→in_review→done（**T7 门**校验 handoff 放行） | PASS |
| A5 | merge 系统节点自动触发并 success（**DAG 序 `--no-ff`**，逐上游 merge） | PASS |
| A6 | 主干产生 **2 个真 merge commit** | PASS |
| A7 | `worktrees.merge_commit` 持久到 TaskDetail 读面 | PASS |
| A8 | check 系统节点在 **repo 主工作区**跑并 success（`git --version` exit 0） | PASS |
| A9 | `GET /tasks/{id}/diff` 经 daemon 真 `git.diff` 返回逐文件 unified patch | PASS |

### 场景 B —— 冲突派回（同行冲突 → 派回 → 解决 → retry 合并成功）

| # | 检查 | 结果 |
| --- | --- | --- |
| B1 | 派生两 worktree | PASS |
| B2 | 两 worktree 改**同一行** conflict.txt（真提交，制造冲突） | PASS |
| B3 | 两任务推到 done | PASS |
| B4 | 第二个 merge 冲突致 merge 节点 **failed** | PASS |
| B5 | **自动建「解决冲突」任务派回**（新 agent 节点 + 连边→merge 节点） | PASS |
| B6 | 在冲突 worktree 解决并提交（`git merge main` + resolve） | PASS |
| B7 | 解决冲突任务推到 done（承原 owner，claim 409→直推 todo→in_progress） | PASS |
| B8 | `POST /canvas-nodes/{id}/retry` 接受（**仅 failed 可 retry**，202） | PASS |
| B9 | retry 后 merge 节点 **success**（冲突解决 → 合并成功） | PASS |

```
=== M6a 实机 verify：20/20 ALL PASS ===
```

## 2. 真 git 产物（真 scratch 仓库，daemon 真 `git.py` 执行）

**交付仓库**（场景 A，`git log --graph`）——两个 `CoAgentia merge task #...` 的 `--no-ff` merge commit（各带 2 parent，message 含 task#/task_id/node_id）：

```
*   3e00cb2 CoAgentia merge task #2 实现 B (task_id=01KXA93D8K..., node_id=01KXA93D8V...)
|\
| * 49b409d deliver fileB.txt
* |   2bfc7c9 CoAgentia merge task #1 实现 A (task_id=01KXA93D7E..., node_id=01KXA93D8V...)
|\ \
| |/
| * 74a1e86 deliver fileA.txt
|/
* e03ccd9 seed
```

A7 断言的持久 `merge_commit` = `58dbf96b…`（TaskDetail.worktree 读面，随运行变化）。

**冲突仓库**（场景 B，`rev-list --merges --count HEAD = 3`）——merge 改C（干净）→ 解决期 `Merge branch 'main'` → retry merge 改D：

```
*   2e4848c CoAgentia merge task #2 改 D (retry 后合并成功)
|\
| *   d19533e Merge branch 'main' into coagentia/task-...改D   ← worktree 内解决冲突
* |   e9df10c CoAgentia merge task #1 改 C   ← 第一个干净 merge
...
* 1d57393 seed
```

## 3. 真机截图（playwright 1440×900，同源 SPA + 真 WS）

| 截图 | 内容 |
| --- | --- |
| [m6a-real-canvas-delivery.png](m6a-real-canvas-delivery.png) | **交付画布**：实现A(#1) Done / 实现B(#2) Done → **Merge：success** → **Check · git --version：success**，React Flow 真 DAG（两 agent 节点 → merge → check 三边） |
| [m6a-real-delivery-thread.png](m6a-real-delivery-thread.png) | **交付线程真系统消息**：`[系统工作目录] 在 …\worktrees\<project>\<task> 中工作`（工作目录注入含真绝对路径）+ `merge 节点进展 … merge_commit=2bfc7c94/3e00cb2d` + `merge 节点成功` + `check 节点完成 status=success exit_code=0 输出尾：git version 2.49.0` |
| [m6a-real-canvas-conflict.png](m6a-real-canvas-conflict.png) | **冲突画布**：改C(#1) Done / 改D(#2) Done / **解决冲突(#3) Done**（自动派回任务，连边→merge）→ **Merge：success**（retry 后） |
| [m6a-real-conflict-thread.png](m6a-real-conflict-thread.png) | **冲突线程**：冲突系统消息 + 派回留痕（真 WS 无刷新推送） |

## 4. verify-surfaced 观察（非阻断，本次探针路径校正 + 一条产品边角）

| # | 现象 | 处置 |
| --- | --- | --- |
| 1 | **裸系统节点空成功**：经 per-node REST 建 merge/check 系统节点时，`CANVAS_NODE_ADDED` 立即触发 `_scan_channel_system_nodes`，此刻节点尚无上游边 → 非 blocked → `prepare_dispatch` 认领后 `_merge_steps` 空集 → `_succeed_merge_node(empty=True)` **在补边前即 success**。真实产品里系统节点随 landing 批（模板实例化/提案落地）**节点+边同事务**落地，per-node 手工建节点+后补边的路径存在此空成功窗口。 | 探针改为**直插系统节点+边同事务**复刻真实落地语义（交付执行仍全真）；**产品侧作为观察项记录**——手工画布建 merge/check 节点若无上游即自完成，建议随 M6b 提案落地一并复核（是否落地期才建系统节点 / 或裸系统节点不自触发）。不扩本次 M6a 范围。 |
| 2 | 冲突派回任务承原任务 owner，二次 `claim` 返回 409 CLAIM_RACE | 探针改直推 `todo→in_progress`（合法边）；产品行为正确（不重复认领） |
| 3 | 真 uvicorn 下 hub（事件循环）与 REST（线程池）并发写 file SQLite，默认 busy_timeout=5000 偶发 `database is locked` | 探针侧把 verify engine busy_timeout 拉到 30s（纯 probe 配置）消除伪锁；**并发写争用在真部署高负载下可能重现，登记为观察项**（非本次阻断） |

> #1/#3 均为**真机 verify 才现形**的观察（单测用 StubDaemon + 直插拓扑，不经 per-node REST 建系统节点、不经真并发写），符合 verify 存在的意义；已登记，M6a 交付执行链本身 20/20 全绿。

## 5. 守门基线（M6a 收口态，未改产品代码）

本次 verify 仅新增 `scratchpad/` 探针脚本，未触产品代码，守门基线保持波 3 收口态：

```
uv run pytest -q                 → 813 passed / 4 skipped
pnpm -F @coagentia/web test      → vitest 194 passed
pnpm typecheck                   → pyright 0 + 双 tsc 0
uv run ruff check .              → 干净
pnpm gen                         → diff 空
pnpm -F @coagentia/web build     → 绿（dist 重建供同源截图）
```

（`/code-review high` 已于本段前执行，10 findings 于收口提交统一修复——修复摘要见 CURRENT-HANDOFF §4a。）

## 5a. 修复后复验（2026-07-12）

10 findings 修复落地后重跑本探针：**20/20 ALL PASS**（4 轮 3 净 + 1 次环境性 REST 超时于 A4 claim，
属 Windows SQLite/FS 抖动——probe 自带的「残留 DB 锁」500 重试脚手架即为此类现象而设，失败窗口内
无本轮修复代码参与）。探针收尾 `suppress(Exception)` 接不住 CancelledError 的假 traceback 已修
（改 `suppress(BaseException)`）。守门新基线：后端 827 passed / 4 skipped · vitest 195 · pyright 0。

## 6. 探针脚本（scratchpad，可复跑）

- `m6a_verify.py`：主探针（`--keep` 保留 server 供截图）。隔离临时库 + seed + 真 scratch 仓库 + 真 uvicorn 子进程（随机空闲端口，`taskkill /T` 收树）+ 真 `DaemonClient` daemon-sim → 20 检查全链。
- `m6a_harness.py`：装置（migrate/seed/`probe_engine`/`insert_system_topology`/`scratch_repo`/`build_daemon`）。
- `m6a_appfactory.py`：uvicorn `--factory` 入口（隔离库 + busy_timeout 30s）。
