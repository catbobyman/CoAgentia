# M6 实机 verify 证据（J12 = PRD M6 出口）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-12（阶段 4，Fable 亲跑） |
| 范式 | 真 uvicorn（`m6a_appfactory:make_probe_app`）+ 真 websockets daemon-sim（`m6a_harness` 真 `git.py` + FakeAdapter 仅桩 LLM turn）+ 真 scratch git 仓库；REST 扮演 Orchestrator/工人（消息/提案/`<control>` 解析/校验/确认/落地/worktree 交付/merge/check/delta 全走**生产代码**，无桩） |
| 脚本 | [`scratchpad/m6_verify.py`](../../scratchpad/m6_verify.py)（`uv run python scratchpad/m6_verify.py [--keep]`） |
| 结果 | **48 / 48 ALL PASS**（原始 = [`M6-VERIFY-results.json`](M6-VERIFY-results.json)） |
| 隔离 | 临时库 alembic head + seed（Orch=role_template_key='orchestrator'/Ada/Ben，4 频道 orchestrated/conflict/direct/repair）+ 独立随机端口 + 独立数据根/daemon 根/scratch 仓库；结束 `taskkill /F /T` 杀树 |
| 截图 | [提案卡+落地叙事（chat，1440×900）](m6-chat-proposal-landed.png) · [落地 DAG（canvas，1440×900）](m6-canvas-landed-dag.png) |

## 1. 场景与 A1–A8 勾销

| 场景 | 内容 | 覆盖 | 结果 |
| --- | --- | --- | --- |
| **S1 拆解全链** | 一句话需求 `decompose` → Orch `<control>` 提案（含显式 merge+check 系统节点）即提案卡（card_kind=proposal）→ awaiting → 人工调整（`remove_node` N3 + `edit_node` N1 标题）确认 202 → 异步落地 landed（landed_hash≠proposal_hash）→ 结构 = 2 agent + merge + check + **汇总节点**（N3 已删）→ 两 writes_code 任务各自 worktree 就位（真目录存在）→ 交付 done → **merge 系统节点 --no-ff 合并 success** → check success → 汇总；「已落地」消息恰一条；全程 WS 事件（draft.presented/landing.completed/node_added×5/worktree.updated×4，无刷新） | **A1**（一句话→提案卡+草稿）**A3**（删节点/改 owner 后落地与调整一致，账本含 proposal_hash/landed_hash/adjustments）**A4**（无上游激活+worktree 就位，下游 blocked） | S1.1–S1.16 全 PASS |
| **S2 冲突派回** | 两任务改同文件 → 交付 → merge **第二分支冲突 → failed**（仅 failed 可 retry）→ **冲突任务自动建卡派回**（task.created+画布节点）→ 手工解决 `merge main`+commit → **retry 202 → merge success**（merge_commit 持久） | **A6**（冲突→派回→解决→合入） | S2.1–S2.6 全 PASS |
| **S3 修复循环** | 无效提案（边引未知节点）→ **repairing**（S1 直投修复提示不进频道流）→ 改好 → awaiting；另路：初提失败→1/2→2/2→**第三败 failed + source 线程系统消息升级 @人类** | **A2**（修复自动改好 + 连续失败升级） | S3.1–S3.4 全 PASS |
| **S4 A5 崩溃重放** | 十节点链条确认落地中，第一个 op 落账即 **`taskkill /F /T` 杀 server 树** → 重启 → **启动扫描续跑 → landed**（前缀 hit 尾段补齐）→ **十节点无重复无缺失** → **「已落地」消息恰一条** | **A5**（落地中 kill→重启补齐，任务无重复无缺失、已落地恰一条） | S4.1–S4.5 全 PASS |
| **S5 delta + O9** | Agent 直接建节点/连边 → **403 rule=O9**（真实节点 id 抵门）；delta `<control>` 即提案卡 → awaiting（delta.proposed）→ **部分接受**（removed_ops=[2,3]）确认 202 → landed（delta_landed_hash≠delta_hash，adjustments=[2,3]）→ **D1 落地 D2 未落地**，剔除清单进线程；base 过期 delta confirm → **409 DELTA_BASE_MISMATCH** + 提案 **failed** + 线程要求重出 | **A6**（Agent 超范围结构变更转 delta 重走确认）+ §9b #18（delta 校验/base 过期/部分接受/O9 拦截） | S5.1–S5.12 全 PASS |
| **S6 single_task** | 小需求单节点提案落地 → **+1 agent、无汇总、无自动 merge** | **A7** | S6.1 PASS |
| **S7 直落** | direct 频道拆解 → **无确认停顿直接 landed** → 账本 **confirmed_by=auto(channel-policy)** | **A8** | S7.1–S7.2 PASS |

## 2. 真实 git 佐证（scratch 仓库 `git log`）

**snake-repo（S1 交付链，--no-ff 双分支合并）**：
```
*   a5227c7 CoAgentia merge task #3 实现贪吃蛇界面层 (…node_id=…CPZF)
|\
| * 9195a68 ui
* | 54fb4bf CoAgentia merge task #2 实现贪吃蛇核心逻辑（含移动与碰撞） (…node_id=…CPZF)
|\ \
| |/
| * 49be66b core
|/
* 41995f6 seed
```
两个 `--no-ff` merge commit（54fb4bf / a5227c7）同挂一个 merge 节点，check 节点跑 `git --version` exit 0。

**conflict-repo（S2 冲突派回→解决→retry）**：
```
daaffce CoAgentia merge task #3 分支乙改 conflict.txt   ← retry 后合入
a2bef21 resolve conflict                                ← 人工解决提交
e2d7837 CoAgentia merge task #2 分支甲改 conflict.txt   ← 首个分支先合
```

## 3. UI 实证（1440×900，同源 SPA 指向隔离库）

- [`m6-chat-proposal-landed.png`](m6-chat-proposal-landed.png)：#orchestrated 频道全流程叙事——Owner 一句话需求 → Orch 提案卡（拆解提案·已落地·5 节点·依赖 4·指纹 #681b40）→ #2 Ada/#3 Ben（Done）/#4 汇总（Todo）→「拆解已落地」→ worktree 工作目录注入 → merge 节点进展（merge_commit=54fb4bf…/a5227c7…）→ merge/check success → **delta 提案（部分接受剔除 #2#3 → #5 撰写发布说明落地）** → **第二 delta 已失败（F9 base 过期）+ 升级人类 alert**。
- [`m6-canvas-landed-dag.png`](m6-canvas-landed-dag.png)：落地 DAG——两 writes_code 任务（Done）汇入 **Merge（success）** → **Check · git --version（success）** → **汇总交付：2 个子任务**（Todo）；另含 delta/F9 探针留下的「人类插入节点」。

## 4. verify-surfaced 观察（非阻断，登记）

- **freshness 门语义对齐**：harness 以 REST 直发 Agent 提案消息，未经适配器 turn 的「读」动作，故提案作者会被 freshness 门扣为 held（裁决 #12 的**正确**行为）。脚本用 `_mark_agent_read`（直写 read_position 至线程最新）复刻「Orchestrator 收注入即读线程摘要」的真实语义后过门——非产品缺陷，是 harness 与真实适配器读路径的差异补偿。
- **系统节点终态命名**：merge/check 成功终态 = `SystemNodeStatus.SUCCESS`（`merged` 是 WorktreeStatus，非节点态）；冲突时 merge 节点 → `failed`（仅 failed 可 retry）。
- **retry 端点**：`POST /canvas-nodes/{id}/retry` 返回 **202**（异步接受，节点转 running），非 200。

## 5. 守门基线（本 verify 时点）

后端 `uv run pytest -q` **943 passed / 4 skipped** · web vitest **349** · pyright 0 + 双 tsc · ruff 干净 · `pnpm gen` 确定 · web build 绿。verify 起的 server/浏览器/daemon-sim 进程已 `taskkill /F /T` 杀树。
