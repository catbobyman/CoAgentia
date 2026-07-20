# TS 迁移总路线与当前状态（TS-MIGRATION-ROADMAP）

> **执行状态：SUPERSEDED / HISTORICAL**
> 2026-07-20 起已由仓库根 [`plan.md`](../../plan.md) v1.0 取代执行权威；本文仅保留当时事实与决策历史。

v1.0 · 2026-07-19 · owner 指示「把所有需要 TS 迁移批的写成文档，并记录当前状态」落笔。
历史定位：上游决策 = DEDAG 裁决 #5「终态架构已定 TS，daemon 先行」；本文当时曾作为剩余路线图，现已失效，后续不得以本文为起点开工。
daemon 先行批的裁决与执行记录见 [TS-MIGRATION-HANDOFF.md](TS-MIGRATION-HANDOFF.md)（含 §8 CR-TS 评审修复批）；日常状态以 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) 为准。

---

## §1 当前状态快照（2026-07-19，HEAD `03330ca`，main 已对齐，未 push）

### 1a 已完成

| 提交 | 内容 |
| --- | --- |
| `1d62b3c` | **TS 迁移批（daemon 先行）**：`apps/daemon-ts` py daemon 全义务面对等（17 指令/5 查询/11 上报/claude+codex 适配器/MCP stdio/五轨缓冲/心跳重连/worktree 后台单车道）；node ≥22 直跑 .ts 零构建零运行时依赖；契约 D v1.0.5 **零修订零帧变化**；win32 校准条款 cal1–cal8；实机 verify 12/12 + 真 claude CLI 冒烟 3/3 |
| `4f7597a` | **CR-TS 评审修复批**：/code-review high 45 候选 → 23 实锤全修（critical=claude stdin EPIPE 崩进程；which/PATHEXT 本机现行缺陷实机复现修复再实证；py-pydantic-fail-closed 面 TS 裸 cast 直通族补齐；`erasableSyntaxOnly` 机制守门入 tsconfig）+ 23 回归测试 |
| `03330ca` | 整理：误入库 pid 残留清除 + .gitignore 补探针产物模式 |

### 1b 守门基线（六门全绿，只增不减）

pytest **977 passed / 4 skipped**（981 收集 = server 668 + **daemon 230** + 契约等 83）· web vitest **266** · **daemon-ts vitest 270 / 4**（迁移批立基线 247，评审批 +23）· typecheck 三 tsc + pyright 全 0（daemon-ts 含 erasableSyntaxOnly）· ruff 净 · `pnpm gen` 确定 · web build 绿。

### 1c Python 存量盘点（全仓 216 个 .py）

| 目录 | .py 数 | 处置 |
| --- | --- | --- |
| `apps/server` | 118 | **批 B 迁移**（最大件，未立项） |
| `apps/daemon` | 48 | **批 A 退役**（TS 对等已验证，等拍板） |
| `packages/contracts` | 19 | **永久保留 Python**（Pydantic 权威，TS 侧 = pnpm gen 生成镜像；裁决已定不迁） |
| `apps/mock-server` | 5 | 批 C 处置（随 server 批退役或保留） |
| `scripts` | 3 | **保留**（export_schemas.py 等 = gen 链的 py 端，契约权威留 py 则 gen 链留 py） |
| `scratchpad` | 23 | 各批实机 verify 探针资产；引用 py daemon/server 内部件的随对应批改指 TS 或退役 |

### 1d 双轨现状

默认启动仍 = **py daemon**（`uv run coagentia-daemon`）；TS daemon 可用（`node apps/daemon-ts/src/cli.ts --server-url … --api-key …`，或包内 bin `coagentia-daemon-ts`）。二者对同一 server、同一契约 D 完全同构，可随时互换。

---

## §2 剩余批次分解

> **执行细化已另立 [TS-DEV-PLAN.md](TS-DEV-PLAN.md)（v1.0，2026-07-19）**：含 server 全域摸底事实包（60 文件 13,396 行/80 端点/hub 3,413 行）、node:sqlite 本机实测、六项裁决预填（D1–D6）、B-cal 校准清单 scal1–8、B1–B9 波次、对拍 oracle 机制、节奏与风险。开工以彼为准，本节保留为概览。

### 批 A：py daemon 退役（前置 = owner 拍板；工作量小，风险低）

| 步 | 内容 | 备注 |
| --- | --- | --- |
| A1 | 默认启动切换 daemon-ts：启动命令/README/使用教程/CURRENT-HANDOFF 常用命令区同步 | node ≥22 为新前置依赖，文档须写明 |
| A2 | 双轨观察期：真机跑 R1/R2 级委派全链（真 CLI 实战）至少一轮，daemon-ts 当主 | 建议至少数日实用无异常再进 A3 |
| A3 | 删除 `apps/daemon`（48 文件）+ pytest **基线显式重置**（981 收集 → ~751，daemon 230 例随退役删除；daemon-ts vitest 270/4 已逐用例对等承接）+ 探针脚本清点（引用 py daemon 内部件的改指 TS 或归档 archive/） | 守门行「只增不减」例外须在 CURRENT-HANDOFF 显式登记，比照 DEDAG 基线重置先例 |
| A4 | 文档收口：教程/00-技术选型/PROJECT-RECORD 补记 | — |

风险登记：挂账 **TS-④**（oauth_token_refresh 帧不应答）与 **TS-⑤**（机器级凭证单向同步腐坏）为 py/TS **同族**缺口，退役不消除也不加重；若在批 A 前修，只须修 TS 侧一处。

### 批 B：server TS 化（未立项；最大件，须先摸底 + 任务书）

对标 daemon 批模式：**五路摸底 → 裁决表 → 校准先行 → 分波迁移 → 实机 verify**。已知可预填的裁决/风险面：

1. **契约权威不动**：packages/contracts（Pydantic）继续为唯一事实源；server TS 化后消费 contracts-ts 镜像（daemon-ts 已趟通 import type + 生成常量模式，直接复用体例）。
2. **最大风险面 = 数据层**：SQLite 驱动与迁移体系选型（alembic 的 TS 等价物 / 迁移脚本按批次显式建表纪律的承接）；**CAS 纪律（条件 UPDATE）与铁律 4（tx.after_commit）必须在新 ORM/驱动上重新校准**——pysqlite 的语义教训不可假设自动成立，需要「K2-cal 级」数据层校准先行批。
3. **冻结七表 drop 归本批清算**（0001 metadata.create_all 依赖使其在 py 侧不可安全 drop——立项时一并处理）。
4. **fingerprint/同构内核双跑守门重裁**：纪律 8 的 py 权威 + ts 镜像 + golden 三处同步在 server TS 化后归属要重定（挂账 TS-① web fingerprint.ts 孤儿也归此处一并处置：恢复双跑或退役）。
5. **REST/WS 契约 B/D 零修订预期**：与 daemon 批同理，帧形与端点形状不许变，实现换语言。
6. **测试对等**：server 668 例逐用例对应移植（daemon 批「py 基线逐用例对应+各文件头登记」的验收模式直接沿用）。
7. **实机 verify**：真 daemon（此时 = daemon-ts）× 真 TS server × 真 git × 浏览器 E2E；**verify 断言面 = 覆盖面**（CR-TS 教训：没断言的字段等于没验，codex 检测面漏网先例）。
8. **CR 评审固定动作**：批收口后 /code-review high + 对抗核实 + 家族化修复，作为流程标准件（daemon 批实证一次评审抓出 1 critical + 10 major）。

### 批 C：mock-server 处置（5 文件，小件）

mock-server 仅显式要求时使用；随批 B 一并决定：TS 重写 / 保留 py / 退役（front-end 契约测试如已全走 fixtures + 真 server，可直接退役）。

### 不迁清单（终态仍含 Python）

- `packages/contracts`（19 文件）：Pydantic 权威 + golden fixtures 生成。
- `scripts`（3 文件）：export_schemas.py 等 gen 链 py 端。
- 结论：**终态「TS 运行时 + py 契约权威」**，全仓 .py 从 216 收敛到 ~22，不追求归零。

---

## §3 挂账关联（详见 CURRENT-HANDOFF §5）

| 项 | 一句话 | 归属批 |
| --- | --- | --- |
| TS-① | web fingerprint.ts 孤儿（双跑 TS 侧失守） | 批 B（双跑守门重裁时处置） |
| TS-② | gen_fixtures.py 滞后 DEDAG | 观察项（勿顺手修） |
| TS-③ | RuntimeAdapter 同名 / STREAM_LINE_LIMIT 双份自持 | daemon-ts 收敛小批或批 A 顺路 |
| TS-④ | oauth_token_refresh 帧不应答（py/TS 同族） | owner 拍板（契约 E 面）；批 A 前修则只修 TS 侧 |
| TS-⑤ | 机器级凭证单向同步腐坏 | owner 拍板同步策略 |
| TS-⑥ | daemon-ts 共享件收敛（kill 包装 ×6 / ProcSettle ×2 / 行分割器 / boundedUtf8Tail / env 块 / isDict） | 专门收敛小批或批 B 期间 |

## §4 等 owner 拍板的决策点

1. **批 A 开闸**：是否现在把默认 daemon 切到 TS（A1+A2 先行，A3 删码可再观察）。
2. **批 B 立项**：server TS 化的时间点（前置无硬依赖，但建议批 A 观察期跑完再开）。
3. **TS-④/⑤ 凭证族**：应答 oauth 帧（契约 E 升版）与凭证同步策略——与批次无关可单独拍。

## §5 事实源指针

- daemon 批裁决表 + win32 校准条款 + 执行/评审记录：[TS-MIGRATION-HANDOFF.md](TS-MIGRATION-HANDOFF.md)
- daemon 实机证据：[../verify/TSDAEMON-VERIFY-EVIDENCE.md](../verify/TSDAEMON-VERIFY-EVIDENCE.md)
- 技术选型决策记（终态 TS）：`engineering_docs/00-技术选型.md` v1.2（工作区，仓外）
- 日常状态：[CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)
