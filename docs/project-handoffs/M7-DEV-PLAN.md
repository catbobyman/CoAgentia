# M7-DEV-PLAN —— 逐模块执行计划与进度表

> 任务书 = [M7-HANDOFF.md](M7-HANDOFF.md)（范围/裁决/出口清单权威）。本文只跟踪执行进度与波次编排。体例同 [M6-DEV-PLAN.md](M6-DEV-PLAN.md)。协作模式 = [COLLAB-MODEL.md](COLLAB-MODEL.md) v2（Fable 单窗编排，owner 拍板 #4）。

## 0. 编排策略（块内分波 + 波间 inline 守门）

**最大外部不确定性 = win32 上长驻 dev server 子进程的真实行为**（端口分配竞态/PORT 环境变量继承/子进程树形态/taskkill 杀树是否波及孙进程/daemon 重启后孤儿进程）。对策沿用 M6 J3-cal 先例：**块 a 波 1 里 K2-cal 先行**——scratch 目录用零依赖命令真机戳五组行为：① `python -m http.server %PORT%` 经 env 注入启动与可达性轮询；② 同端口双开的失败形态；③ `taskkill /F /T` 对 `cmd /c` 包裹命令的孙进程覆盖；④ 模拟 daemon 崩溃后孤儿 dev server 存活性与清理手段；⑤ 健康检查轮询间隔与超时的合理默认。坑与结论写 `scratchpad/PREVIEW-CALIBRATION.md`（K2/K3 的权威实现参考），把 K2 从「探测未知」降为「照已知行为填空」。

**模型分派（COLLAB-MODEL v2）**：K2-cal + K2 审查 + K9 实机 verify + 幂等/对账正确性修复 = Fable 亲做；K0/K1/K3/K4/K5/K6/K7/B-M7-1/B-M7-2 执行与全部评审 finder/verifier = Opus 子代理；K8 审查文档 = Opus 初稿 + Fable 终审；存疑终裁权保留 Fable。

### 块 M7a「预览链」

| 波 | 模块（并行） | 文件域（不相交） | 依赖 |
| --- | --- | --- | --- |
| **波 1 地基** | K0 契约 ∥ K1 迁移+ORM ∥ **K2-cal 长驻子进程实测校准** | packages/contracts(+ts,+mock) / apps/server(db,migrations) / scratchpad（零产品代码） | 无（三者互不依赖） |
| — inline 守门 1 | 主循环跑 pytest+gen+typecheck；修集成缝 | — | 波 1 全绿才进波 2 |
| **波 2 执行域** | K2 daemon 预览进程域 ∥ B-M7-1 前端（吃 K0 mock） | apps/daemon(preview.py+处理器) / apps/web | K2 吃 K2-cal 结论+K0；B-M7-1 吃 K0 |
| — inline 守门 2 | 全量测试+gen | — | 全绿 |
| **波 3 服务域** | K3 server 预览域（端点/回收调度/对账 #9/广播） | apps/server(routes/tasks preview 端点+computers/hub 调度与对账+ws) | 吃 K1+K2 上报形状 |
| — inline 守门 3 | 全量测试+typecheck+ruff+gen+双 build | — | 全绿 |
| **实机 verify（M7a 出口）** | scratch repo：交付 → 预览打开 → 健康检查 → iframe HTTP 200 → 并排双预览 → idle 回收；坏命令失败态日志尾（§9a #7） | — | Fable 亲跑 |
| **/code-review high** | 按维度 finder（Opus）→ 对抗核实 → Fable 终裁修复 | — | 块 a 收口 |

- 契约集中：**K0 独占 packages/contracts 全部改动**（含 conformance 双跑扩展与 trigger_deploy 工具目录登记——K5 只写 daemon 侧消费），后续波 agent 不碰 contracts。
- K1 独占 models.py（preview_sessions ORM + M7A_TABLES）；K3 只消费。
- daemon 文件域：K2 新建 `preview.py`（进程管理）与处理器挂接；不碰 git.py/既有处理器。
- route 注册：K3 扩既有 routes/tasks.py（preview 三端点挂任务资源下），hub 调度扩 computers/hub.py 既有周期扫描——**块 a 无新 route 文件**。

### 块 M7b「部署、成本与收尾」（块 a 收口后开工）

| 波 | 模块（并行） | 文件域（不相交） | 依赖 |
| --- | --- | --- | --- |
| **波 1 部署链** | K4 0011 迁移 + 部署域全链 | apps/server(migrations+routes/deployments.py 新建+computers/hub 对账 #10+ws 订阅流)+apps/daemon(deploy 处理器+缓冲) | 吃 K0 形状 |
| — inline 守门 | 全量测试+gen | — | 全绿 |
| **波 2 工具与账务** | K5 trigger_deploy 工具 ∥ K6 成本核算 ∥ B-M7-2 前端 | apps/daemon(mcp.py) / apps/server(routes/usage.py 新建+deployments token_summary 挂点) / apps/web | K5/K6 吃 K4；B-M7-2 吃 K0 mock+K4 端点 |
| — inline 守门 | 全量测试+typecheck+ruff+gen+双 build | — | 全绿 |
| **波 3 收尾件** | K7 性能小批 ∥ K8 预留位审查文档 | apps/server(四处既有文件小改) / docs（零代码） | 独立；K7 与波 2 文件域有交叠故排后 |
| — inline 守门 | 全量测试（行为等价回归）+查询次数断言 | — | 全绿 |
| **实机 verify（K9 = PRD M7 出口）** | 端到端：需求 → 拆解 → 执行 → Diff/预览验收 → 合并 → 部署（人类+Agent 双通道/409/日志流/URL/新账小结）→ 回收；对账 #9/#10 崩溃探针（§9b #16） | — | Fable 亲跑 |
| **/code-review high** | finder+verifier 全 Opus，Fable 终裁 → 修复 → 守门 | — | **M7 里程碑收口 = MVP 全里程碑完成**（任务书移 archive/） |

- K4 独占 models.py 本块改动（deployments ORM + M7B_TABLES）与 routes/deployments.py 新建（挂 routes/__init__.py——**块 b 唯一一处**）。
- K6 新建 routes/usage.py（第二处 route 注册，波 2 与 K4 波 1 错开无竞争）；token_summary 计算函数放 deployments 域、K6 只挂调用（文件域按波序交接）。
- K7 四件全是既有文件小改（ledger/service、computers/hub、routes/serialize、routes/search）——**必须排在波 3**（波 1/2 稳定后动，避免并行改同文件）。

## 1. 进度表

| # | 模块 | 状态 | 提交 | 备注 |
| --- | --- | --- | --- | --- |
| — | 立项：契约六面落笔（A v1.0.11/B v1.5/D v1.0.4/E v1.5；C/E2 零修订核对）+ 任务书 + 本计划 | ✅ | 本提交 | owner 六拍板（任务书 §7 #1–#6）；A/B/D 矛盾纠偏（排队措辞）随立项收口 |
| K2-cal | 长驻 dev server win32 实测校准 → `scratchpad/PREVIEW-CALIBRATION.md` | ✅ | 5/5 探针 | Fable 亲跑（§3 已填）；关键坑=win32 SO_REUSEADDR 同端口双绑不被 OS 拒绝→daemon 自持端口唯一性 |
| K0 | 契约登记（ENDPOINTS_M7/四模型/D 帧/工具目录/ws 核对/mock/conformance） | ✅ | 波1 | 7 端点+UsageBucket/TasksReporting/TokenSummary/UsageReport；trigger_deploy(16)；token_summary 收紧 TokenSummary\|None；ws 零改动(forward-freeze 已登记)；contracts 91 pass |
| K1 | 0010 迁移（preview_sessions+单活跃索引）+ ORM | ✅ | 波1 | 部分唯一索引谓词单源 `_PREVIEW_ACTIVE_WHERE`(主动解 CR-10)；M7A_TABLES；alembic 63 pass+红例 |
| K2 | daemon 预览进程域（PORT 注入/健康检查/存活监控/start·stop 处理器/status 上报） | ✅ | 波2 | PreviewRunner 长驻变体；端口注册表自持唯一性；健康-存活并行竞速；shutdown 杀子无孤儿；daemon 154 pass；**Fable 亲审通过（端口释放恰一次逐交错核对）** |
| K3 | server 预览域（ensure+touch/回收三触发/对账 #9/广播/拒绝路径） | ✅ | 波3 | 端点/CAS 条件 UPDATE/回收三触发/对账 #9；**Fable 亲修 3 项**：pre-commit 下发竞态→after_commit 硬保证 / 补 preview.failed 诊断(FR-11.3) / test_preview 跨 app 同名冲突；后端 1001 pass |
| B-M7-1 | 前端预览面板（按钮/三态顶条/失败日志尾/心跳/并排/倒计时/wsBridge） | ✅ | 波2 | PreviewPanel+PreviewDeck 并排/canPreview 门/60s 心跳/wsBridge preview.updated；web 375（+16） |
| — | M7a 实机 verify（§9a #7）→ 证据归档 | ✅ | 14/14 | Fable 亲跑：真 uvicorn+真 daemon-sim+真 PreviewRunner；真起 dev server→健康检查→**iframe HTTP 200**→并排端口互异→ensure+touch→idle 回收+杀进程→坏命令 failed；孤儿 0；[M7-EVIDENCE.md](../verify/M7-EVIDENCE.md) |
| — | /code-review high（块 a）→ 修复 | ✅ | 7 CONFIRMED 全修 | 8 维 Opus finder→对抗核实→Fable 终裁：15 findings/7 CONFIRMED；**Fable 亲修 4 correctness**（reconnect 泄漏→daemon 断连对称杀预览 / 心跳复活 failed→isActive 守护 / idle_min=0 当 30→is not None / 前端）+ Opus 补 5 回归/覆盖测试；后端 1006 pass |
| K4 | 0011 迁移 + 部署域全链（端点/409+兜底/日志链路/结果卡/对账 #10/订阅流） | ☐ | — | 块 b 波 1 |
| K5 | trigger_deploy 工具（mcp.py +1/透传/话术/E2 核对） | ☐ | — | 波 2 |
| K6 | 成本核算（GET /usage 三层/token_summary 新账快照） | ☐ | — | 波 2 |
| B-M7-2 | 前端部署卡+成本面（确认弹窗/日志跟随/token 小结/画布汇总条/wsBridge） | ☐ | — | 波 2 |
| K7 | 性能小批四件（CR-9/usage.batch/serialize/search 双扫，行为等价） | ☐ | — | 波 3 |
| K8 | 预留位审查文档 `docs/M7-RESERVATION-AUDIT.md` | ☐ | — | 波 3 |
| K9 | 实机 verify = PRD M7 出口（§9b #16）→ `docs/verify/M7-EVIDENCE.md` | ☐ | — | Fable 亲跑 |
| — | /code-review high（M7 收口）→ 修复 → 守门 → 任务书归档 | ☐ | — | **= MVP 全里程碑完成** |

## 2. 防返工锚点（写代码前先读）

| 域 | 锚点 | 用途 |
| --- | --- | --- |
| 子进程管理 | daemon `check.run` 处理器（30min 超时/幂等/终态重发）+ `scratchpad/GIT-CALIBRATION.md`（win32 杀树/编码结论） | K2 长驻变体的骨架与坑库 |
| 周期调度 | computers/hub.py keep_days 清理扫描 + `_landing_loop` + 60s 对账 | K3 回收调度与对账 #9/#10 挂同一循环，勿另起调度器 |
| 缓冲重传 | daemon buffer usage.batch 体例（JSONL 落盘/ack 重传） | K4 deploy.log 批同体例；chunk_seq 去重在 server 侧 |
| 订阅流 | ws/hub.py diagnostic 流 sub/unsub | K4 deploy_log 流照抄结构 |
| 结果卡 | persist_message card_kind 两相挂接（M6b）+ 前端 MERGE_CONFLICT 卡判定链 | K4/B-M7-2 零新机制 |
| usage 聚合 | routes/tasks.py:414 TaskDetail.usage SQL | K6 三层聚合同源改 GROUP BY；禁 UPDATE/DELETE 触发器保证实时推导恒正确 |
| MCP 工具 | daemon mcp.py claim_task（错误结构化透传体例）+ contracts COAGENTIA_MCP_TOOLS/catalog 测试 | K5 照抄 +1 |
| 幂等键 | POST 写端点 Idempotency-Key 账本复用（B §1） | K4 直接复用，勿造新机制 |
| CAS/条件 UPDATE 纪律 | CURRENT-HANDOFF §8「凡状态机边写必条件 UPDATE」（M6 三度印证） | deployments/preview_sessions 状态推进一律 WHERE status=起态；两处部分唯一索引是兜底不是替代 |
| 实机基建 | `scratchpad/m6a_harness.py`/`m6a_appfactory.py`/`m6_verify.py` | K9 扩探针；dev_command 零依赖命令见 K2-cal |

## 3. K2-cal 校准结论（✅ 已填；K2/K3 的权威实现参考 = `scratchpad/PREVIEW-CALIBRATION.md`）

> 2026-07-13 真机 5/5 探针 `passed: true`（`scratchpad/preview_calibration.py` 可复跑），无孤儿泄漏。全文结论见 [PREVIEW-CALIBRATION.md](../../scratchpad/PREVIEW-CALIBRATION.md)。摘要如下——K2 从「探测未知」降为「照已知行为填空」：

| 探针 | 结论 | K2 实现约束 |
| --- | --- | --- |
| 端口分配+PORT 注入 | `bind :0→getsockname→close` 取端口；`create_subprocess_shell`+`env["PORT"]`，命令引用 `%PORT%` 由 cmd.exe 展开，dev server 亦可读 `process.env.PORT` | **必须用 shell 非 exec**（%PORT% 展开 + npm run dev 类命令） |
| **同端口双开（最关键坑）** | **Windows SO_REUSEADDR 允许同端口双绑成功**（http.server/Vite 默认设该选项，Unix 不允许）；OS 不拒绝、健康检查对双方都通过 | **daemon 进程内 `assigned_ports` set + asyncio.Lock 自持端口唯一性**（撞则重取），不能靠 OS 拒绝或「第二进程崩溃」信号 |
| taskkill 杀孙 | `taskkill /F /T /PID <shell_pid>` 连 python 孙进程一并杀，端口释放（`checks.py:_kill_process_tree` 同款） | 杀树 key = daemon 持有的 shell 进程 PID |
| daemon 崩溃孤儿 | **win32 asyncio 子进程不随父退出自动死**；清洁关闭须逐个杀子（`CheckRunner.wait_closed` 先例），硬崩溃孤儿存活并占端口 | **shutdown handler 逐个 taskkill 活跃预览**；对账 #9 硬崩溃孤儿 fail-closed 置 failed（端口由 pick_free_port 自然规避，孤儿泄漏 MVP 接受，登记 K8） |
| 存活监控+坏命令 | 坏命令先退出（先于 120s 健康超时）；log_tail 捕获进程输出尾 ≤2KB | 健康检查 vs `proc.wait()` **并行竞速** `asyncio.wait(FIRST_COMPLETED)`；`_bounded_utf8_tail` 复用 |

**参数默认**（实现默认非协议形状）：健康检查轮询 0.5s / 超时 **120s**（D §5.3）/ log_tail **2KB** / 前端心跳 60s（B §13.1）。
