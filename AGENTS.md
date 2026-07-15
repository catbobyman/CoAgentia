# AGENTS.md —— CoAgentia 仓库工作规程

本机多 Agent 协作 IM（FastAPI + React + SQLite monorepo，Windows 单机 MVP）。**契约驱动开发**：实现是契约的填空。

## 开工必读（按序）

1. `docs/project-handoffs/CODEX-CONTEXT.md` —— **完整项目上下文**（术语解码/架构/纪律/坑清单，为你而写）
2. `docs/project-handoffs/CURRENT-HANDOFF.md` —— 当前状态
3. `docs/project-handoffs/M6-HANDOFF.md` —— 当前任务书（范围/模块 DoD/裁决/出口清单）
4. `docs/project-handoffs/M6-DEV-PLAN.md` —— 执行计划与进度表（**每完成一个模块必须更新**）
5. 做哪个模块，精读对应契约：`D:\Project4work\Agenthub_7_8\engineering_docs\`（A 表/B REST/C WS/D daemon/E·E2 适配器）；M6b 另读 `D:\Project4work\Agenthub_7_8\orchestrator_docs\Orchestrator任务拆解设计.md`

## 守门命令（每个模块/波次收口全绿才算完成；基线只增不减）

```bash
uv run pytest -q                    # 起点 712 passed / 4 skipped
pnpm -F @coagentia/web test         # 起点 175
pnpm typecheck                      # pyright 0 + 双 tsc
uv run ruff check .
pnpm gen                            # 之后 git diff 必须为空
pnpm -F @coagentia/web build
```

## 铁律（违反=返工，详解见 CODEX-CONTEXT §6/§8）

- **契约先行**：改行为先升契约版本+变更记录再动代码；未列出的表/字段/帧/错误码/工具**不要发明**。
- `packages/contracts-ts/src/generated` 只经 `pnpm gen` 生成，勿手改。
- mock 只是形状源；业务逻辑只活在真 server/daemon；新端点扩 `test_conformance_dual.py`。
- 确定性内核单源：graph/fingerprint/decomposition = py 权威 + ts 镜像 + `packages/fixtures/golden/` 判例双跑，改语义三处同步。
- 迁移：按批次显式点名建表（勿 metadata.create_all 全集）；既有表加索引须 `if_not_exists`；从零与增量升级双路测试。
- daemon 是执行器不是决策者：一切判定（gating/DAG 序/冲突处置/触发）在 server。
- win32：杀子进程 `taskkill /F /T`；子进程 stdout 显式 utf-8 decode。
- 挂账清单里的已知问题，任务书没点名就**不要顺手修**。

## 协作约定（owner 偏好）

- 全程**中文**交流与文档。
- 小瑕疵直接修；大方向给选项问 owner；**已拍板的不再问**（M6 四拍板见任务书 §7 #1–#4）。
- 结论要实证：实机 verify 隔离库+独立端口+截图，证据归 `docs/verify/`；实机起的进程收尾必须杀掉。
- 每完成一个模块：更新 M6-DEV-PLAN 进度表（状态+提交哈希）与 CURRENT-HANDOFF；阶段结论写 PROJECT-RECORD。
- git：仓库无 remote，提交仅本地 main；逐波提交，工作树保持干净。
