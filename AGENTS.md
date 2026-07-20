# AGENTS.md —— CoAgentia 仓库工作规程

本机多 Agent 协作 IM（当前为 FastAPI + React + SQLite monorepo，正在按批准计划迁移到纯 TypeScript）。**契约驱动开发**：实现是契约的填空。

## 开工必读（按序）

1. [plan.md](plan.md) —— **全量 TypeScript 迁移唯一执行权威**；先确认当前阶段、前置条件和 owner gate。
2. `docs/verify/ts-migration/PLAN-AUTHORITY.md` —— 权威边界与历史文档状态。
3. `docs/project-handoffs/CURRENT-HANDOFF.md` —— 当前 Git、守门和阻塞快照。
4. 当前阶段证据/任务书；P0 为 `docs/verify/ts-migration/P0-BASELINE.md`。
5. 需要历史上下文时再读 `docs/project-handoffs/CODEX-CONTEXT.md`；做产品行为时精读 `D:\Project4work\Agenthub_7_8\engineering_docs\` 对应 A/B/C/D/E/E2 契约。

`docs/project-handoffs/TS-MIGRATION-ROADMAP.md`、`TS-DEV-PLAN.md` 及 M1–M8 文档均为 historical/reference，不得覆盖根计划的范围、依赖或阶段状态。

## 守门命令（每个模块/波次收口全绿才算完成；基线只增不减）

```bash
uv run pytest -q                    # P0 clean 基线：977 passed / 4 skipped
pnpm -F @coagentia/web test         # P0 clean 基线：266
pnpm -F @coagentia/daemon-ts test   # P0 clean 基线：270 passed / 4 skipped
pnpm typecheck                      # pyright 0 + 全部 tsc
uv run ruff check .
pnpm gen                            # 之后 git diff 必须为空
pnpm -F @coagentia/web build
pnpm verify:p0                      # P0 机器母账、清单、权威与 mutant 门
```

## 铁律（违反=返工，详解见 CODEX-CONTEXT §6/§8）

- **契约先行**：改行为先升契约版本+变更记录再动代码；未列出的表/字段/帧/错误码/工具**不要发明**。
- `packages/contracts-ts/src/generated` 只经 `pnpm gen` 生成，勿手改。
- mock 只是形状源；业务逻辑只活在真 server/daemon；新端点扩 `test_conformance_dual.py`。
- 确定性内核单源：graph/fingerprint/decomposition = py 权威 + ts 镜像 + `packages/fixtures/golden/` 判例双跑，改语义三处同步。
- 迁移：按批次显式点名建表（勿 metadata.create_all 全集）；既有表加索引须 `if_not_exists`；从零与增量升级双路测试。
- daemon 是执行器不是决策者：一切判定（gating/DAG 序/冲突处置/触发）在 server。
- win32：杀子进程 `taskkill /F /T`；子进程 stdout 显式 utf-8 decode。
- 挂账清单里的已知问题，根计划当前阶段没点名就**不要顺手修**。
- P0～D 期间不得绕过 `verify:test-ledger` / `verify:migration-inventory` 删除、改名或漏记旧实现；不可逆删除只在根计划对应 owner gate 后执行。

## 协作约定（owner 偏好）

- 全程**中文**交流与文档。
- 小瑕疵直接修；大方向给选项问 owner；根计划已经拍板的终态和 P0 不重复询问，未拍板的发布渠道/不可逆 gate 不擅自决定。
- 结论要实证：实机 verify 隔离库+独立端口+截图，证据归 `docs/verify/`；实机起的进程收尾必须杀掉。
- 每完成一个迁移波：更新根 `plan.md` 进度账本与 `CURRENT-HANDOFF.md`；阶段结论和验收证据写入 `docs/verify/ts-migration/`。
- git：当前存在 `origin`，但没有 owner 明示授权不得 push；逐波本地提交，工作树保持干净。
