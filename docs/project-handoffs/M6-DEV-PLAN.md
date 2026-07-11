# M6-DEV-PLAN —— 逐模块执行计划与进度表

> 任务书 = [M6-HANDOFF.md](M6-HANDOFF.md)（范围/裁决/出口清单权威）。本文只跟踪执行进度与波次编排。体例同 [M5-DEV-PLAN.md](M5-DEV-PLAN.md)。

## 0. 编排策略（多 agent 并行，块内分波 + 波间 inline 守门）

**最大外部不确定性 = win32 上 `git worktree` 的真实行为**（路径分隔/长路径/中文路径/锁文件/进程占用删除失败/CRLF）。对策沿用 M5 先例（codex 协议立项会话先校准）：**块 a 波 1 里 J3-cal 先行**——scratch repo 真机戳 `worktree add/remove`、`merge --no-ff`、冲突/abort、`diff --numstat/-p` 四组操作，坑与结论写 `scratchpad/GIT-CALIBRATION.md`（J3/J4/J5 的权威实现参考），把 J3 从「探测未知」降为「照已知行为填空」。

### 块 M6a「Project 与交付链」

| 波 | 模块（并行） | 文件域（不相交） | 依赖 |
| --- | --- | --- | --- |
| **波 1 地基** | J0 契约 ∥ J1 迁移+ORM ∥ **J3-cal git 实测校准** | packages/contracts(+ts,+mock) / apps/server(db,migrations) / scratchpad（零产品代码） | 无（三者互不依赖） |
| — inline 守门 1 | 主循环跑 pytest+gen+typecheck；修集成缝 | — | 波 1 全绿才进波 2 |
| **波 2 执行域** | J2 Project 域 ∥ J3 worktree 生命周期 | apps/server(routes/projects) / apps/daemon(git.py+处理器)+apps/server(hub 激活联动·对账 #5·注入话术) | J2/J3 吃 J0+J1；J3 吃 GIT-CALIBRATION |
| — inline 守门 2 | 全量测试+gen | — | 全绿 |
| **波 3 交付面** | J4 Diff 链路 ∥ J5 系统节点执行与合并链 ∥ J6 评审结论枚举 ∥ B-M6-1 前端 | apps/daemon(diff)+apps/server(routes/tasks diff) / apps/server(canvas 触发器+hub merge 调度) / apps/server(contracts 消费+builtin 话术) / apps/web | J4/J5 吃 J3 的 git.py；J6 独立；B-M6-1 吃 J0 mock 形状 |
| — inline 守门 3 | 全量测试+typecheck+ruff+gen+双 build | — | 全绿 |
| **实机 verify（M6a 出口）** | scratch repo + 两并行 writes_code 任务 → worktree 交付 → Diff 评审 → merge --no-ff → check 绿；冲突→派回→解决→retry | — | §9a #11 |
| **/code-review high** | 8 角度 review → 修 | — | 块 a 收口 |

- 契约集中：**J0 独占 packages/contracts 全部改动**（含 conformance 双跑扩展），后续波 agent 不碰 contracts。
- J1 独占 models.py（三表 ORM + tasks 两列 + M6A_TABLES），J2/J3 只消费。
- daemon 文件域：J3 建 `git.py` 与 worktree 处理器；J4 只**追加** diff 函数与查询帧处理，不改 J3 已写文件的既有函数（波序保证）。
- route 注册：J2 新建 routes/projects.py（需挂 routes/__init__.py——**块 a 唯一一处**，J2 独占）；J4 扩既有 tasks.py；J5 扩既有 canvas.py。

### 块 M6b「Orchestrator 拆解链」（块 a 收口后开工）

| 波 | 模块（并行） | 文件域（不相交） | 依赖 |
| --- | --- | --- | --- |
| **波 1 内核** | J7 同构校验内核 ∥ J11 角色模板+模板治理 | packages/contracts(kernel)+apps/web(lib)+fixtures(golden) / apps/server(templates 域+builtin 记录) | J7 无依赖；J11 独立域 |
| — inline 守门 | pytest+vitest（golden 双跑）+gen | — | 全绿 |
| **波 2 提案域** | J8 0009 迁移+提案生命周期 | apps/server(migrations+orchestration/proposal+messages 解析挂接+hub 直投/对账 #6) | 吃 J7 内核 |
| **波 3 确认与落地** | J9 草稿确认+落地事务+fail-closed 复核 ∥ B-M6-2 前半（拆解入口/提案卡/wsBridge 事件族） | apps/server(orchestration/draft·landing) / apps/web | J9 吃 J8；B-M6-2 吃 J0 mock |
| **波 4 增量** | J10 delta+O9 拦截 ∥ B-M6-2 后半（草稿层/delta 部分接受面板/P12 编排组） | apps/server(orchestration+canvas 403 门) / apps/web | J10 吃 J9 |
| — inline 守门 | 全量测试+typecheck+ruff+gen+双 build | — | 全绿 |
| **实机 verify（J12 = PRD M6 出口）** | 三场景全链 + single_task/直落/delta/A5 崩溃重放（拆解设计 A1–A8 逐条勾销） | — | §9b #21 |
| **/code-review high** | 8 角度 review → 修 | — | M6 里程碑收口 |

## 1. 进度表

| # | 模块 | 状态 | 提交 | 备注 |
| --- | --- | --- | --- | --- |
| J3-cal | git 实操 A 级实测校准 → `scratchpad/GIT-CALIBRATION.md`（win32 worktree/merge/diff/冲突行为与坑清单） | ✅ | 波 1 待提交 | 10/10 探针绿；见 §3 校准结论 |
| J0 | 契约登记（ENDPOINTS_M6/Project·Worktree·Diff·Proposal 模型/Task 系扩字段/ReviewVerdict/错误码 28/ws 预留族核对/D 新帧/mock/conformance） | ✅ | 波 1 待提交 | A v1.0.8/B v1.4.1 补遗同步；focused 135 绿；gen 二次确定 |
| J1 | 0008 迁移（projects+channel_projects+worktrees 三表 + tasks 两列）+ models ORM + M6A_TABLES | ✅ | 波 1 待提交 | 含 merge_commit；从零+历史 M5 schema 切片增量双路绿 |
| J2 | Project 域（CRUD/admin 门/repo_path 校验/PROJECT_IN_USE/频道绑定/channel_ids 派生） | ⬜ | — | 块 a 波 2 |
| J3 | worktree 生命周期（daemon git.py+ensure/cleanup 处理器+status 上报；server 激活联动+对账 #5+keep_days 清理调度+工作目录消息注入） | ⬜ | — | 块 a 波 2 |
| J4 | Diff 链路（daemon git.diff 查询帧 DiffPayload + GET /tasks/{id}/diff 代理 + TaskDetail.worktree 派生） | ⬜ | — | 块 a 波 3 |
| J5 | 系统节点执行与合并链（自动触发器/check.run·check.finished/retry 仅 failed/merge DAG 序 --no-ff/冲突自动建任务派回/全留痕） | ⬜ | — | 块 a 波 3 |
| J6 | 评审结论枚举（review_verdict 四值/needs_human @人类/builtin 评审话术更新/human_members DRY 顺路评估） | ⬜ | — | 块 a 波 3 |
| B-M6-1 | 前端：频道设置 Project 组+Project CRUD 弹窗/Diff 卡/系统节点形态+Retry/verdict 徽标/冲突卡/wsBridge worktree.updated | ⬜ | — | 块 a 波 3 |
| — | **M6a 实机 verify** → `docs/verify/M6A-EVIDENCE.md`（§9a #11 场景+截图） | ⬜ | — | 块 a 出口 |
| — | /code-review high（块 a） | ⬜ | — | 块 a 收口 |
| J7 | 同构校验内核（kernel decomposition：V1–V14+`<control>` 解析+指纹；lib/decomposition.ts 镜像；golden/decomposition.json 双跑） | ⬜ | — | 块 b 波 1 |
| J11 | Orchestrator 角色模板（数据+创建预选+NO_ORCHESTRATOR 引导面）+ 模板 PATCH/DELETE（builtin 409） | ⬜ | — | 块 b 波 1 |
| J8 | 0009 迁移 + 提案域（8 态状态机/三入口归一/上下文注入/`<control>` 解析挂接/修复循环 S1 直投/Superseded/对账 #6/24h 提醒） | ⬜ | — | 块 b 波 2 |
| J9 | 草稿确认与落地（confirm CAS/调整重验/adjustments 落账/落地事务 decomp: 幂等/汇总+merge 节点自动追加/直落/**fail-closed 复核必做**/对账 #4/A5） | ⬜ | — | 块 b 波 3 |
| J10 | delta 增量（base 指纹/结果图重验/NODE_ACTIVE/部分接受 removed_ops/幂等）+ O9 拦截（Agent 结构写 403 rule=O9） | ⬜ | — | 块 b 波 4 |
| B-M6-2 | 前端：拆解入口+引导/提案卡/草稿层防呆确认/delta 面板部分接受/rev 替换/P12 编排组/wsBridge draft.*·delta.*·landing.*·proposal.updated | ⬜ | — | 块 b 波 3–4 |
| J12 | **实机 verify = PRD M6 出口** → `docs/verify/M6-EVIDENCE.md`（三场景+A1–A8+截图） | ⬜ | — | 块 b 出口 |
| — | /code-review high（块 b）→ M6 里程碑收口，任务书移 archive/ | ⬜ | — | 终收口 |

## 2. 守门命令（波间与收口；全绿才算过门）

```
uv run pytest -q                    # 712 passed / 4 skipped 起点基线，只增不减
pnpm -F @coagentia/web test         # vitest 175 起点基线
pnpm typecheck                      # pyright 0 + 双 tsc
uv run ruff check .
pnpm gen                            # 后 git diff 应为空（生成物确定性）
pnpm -F @coagentia/web build
```

## 3. 关键实现锚点（防返工；行号 = 立项实核 2026-07-11）

- **落地事务器/账本**：`ledger/service.py`（record:112 / lookup:159 / create_batch:191 / mark_done:224 / mark_fail_closed:233）+ `ledger/replay.py`（HandlerRegistry:40 / done_op_id:62 / replay_batch:68）。**J9 = 照 `templates/service.py:531 instantiate_template` 体例写第二个落地批消费者**（reserve-before 幂等/req_hash 折 source id/校验全前置于 reserve——M5b code-review 教训）。
- **系统节点壳三面已就位**（M3b）：models.py:564-578 列 / routes/canvas.py:112 create_node（kind=system → system_status=idle）/ canvas/service.py:128-148 derive_blocked（system success=satisfied）——J5 纯增执行触发与状态推进，勿动创建面。
- **S1 直投先例**：hub.py:1011 `inject_guard_feedback`——J8 修复循环 = source.kind=repair 同款路径（不进频道流+诊断双写）。
- **对账挂接点**：hub.py:829 `reconcile`——#5 worktree 补派生（J3）/#6 修复续传（J8）/#4 落地重放（J9）三条 D §4.4 已登记，接真即可。
- **内核双跑范式**：kernel/graph.py+fingerprint.py ↔ web lib/graph.ts ↔ fixtures/golden/*.json——J7 照此加第三组 decomposition；**指纹复用 fingerprint.py 勿重写**。
- **频道编排配置列 M1 已建**：models.py:245-246 decomp_mode/decomp_node_limit（enums.py:254 DecompMode）——J8 直落判定/V6 上限直接读列。
- **daemon 骨架复用**：查询帧照 home.tree/home.file 体例（git.diff）；缓冲重传照 usage.batch 体例（check.finished）；win32 子进程坑 = M5 先例（taskkill /F /T 杀进程树、stdout 必须 utf-8 decode——git 输出同样适用）。
- **幂等身份纪律**（M5b #B 教训）：落地批 req_hash 必须折入 source 身份（decomp=proposal id+landed_hash / delta=delta_landed_hash），防跨提案同键错批重放。
- **重放保序**（M5b #C 教训）：批内节点顺序以 ledger `seq` 为准，勿按 (created_at, op_id) 字典序。
- **win32 git 已校准**（J3-cal，2026-07-11）：权威记录 = [`scratchpad/GIT-CALIBRATION.md`](../../scratchpad/GIT-CALIBRATION.md)，复现脚本 = `scratchpad/run-git-calibration.ps1`，最终 10/10 探针绿。关键边界：① 正/反斜杠与中文路径均可，但 `worktree list --porcelain` 统一正斜杠；② 本机 worktree 根 193 字符成功、214 字符起即使 `core.longpaths=true` 仍报 `'$GIT_DIR' too big`，契约 ULID 路径必须保持短组件且不硬编码本机阈值；③ 显式 lock/index.lock 分别令 remove/add 退出 128，不盲目删锁；④ 文件句柄占用时 remove 退出 255，**Git 登记已消失但物理目录残留**，cleanup 必须登记+目录+prune 三面幂等；⑤ 冲突文件必须在 `merge --abort` 前用 `--diff-filter=U` 采集；⑥ Diff 元数据使用 `--name-status -z`/`--numstat -z`，二进制 `-/-` 映射 0/0+空 patch；⑦ git stdout/stderr 显式 UTF-8，默认 GB2312 会乱码；PowerShell 5.1 的 UTF-8 脚本需 BOM。
