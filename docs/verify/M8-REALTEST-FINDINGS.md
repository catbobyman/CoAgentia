# M8 上线前实机测试 — 第一阶段发现与全链实证（真 CLI 多 Agent）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-14~15（真机会话，Owner 授权自主推进） |
| 环境 | 真 uvicorn server(8787) + 真 web(5173) + 真 daemon + 真 claude(2.1.208)/codex(0.144.0) CLI；默认库迁移 0011→0012（备份 `coagentia.pre-m8-migrate.*.db` / `coagentia.pre-surgery.db`） |
| 定位 | 首次以**真 CLI**（非 daemon-sim FakeAdapter）跑完「需求→拆解→落地→交付→合并→O8 汇总」全链。历届 M6/M7/M8 verify 全用 FakeAdapter；本次填补真机空白，方案 = [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) T-A′ 变体 |
| 一句话结论 | **全链在真 CLI 下端到端贯通**（两处需人工辅助绕过）：交付链闭环到主干 3 个 DAG 序 merge commit + O8 总报告。发现 3 个 sim 掩盖的真机专属 blocker——投递前缀死锁 B-1、Agent 无契约提交通道 B-5、codex 适配器挂死 B-4——**均已修并生产/真机验证（2026-07-15）**；另记 B-6[低,话术调优]交付收尾冷启动。详见 §0′（B-1+B-5 复跑）与各 finding。 |

## 0′. B-1 + B-5 修复后零(近)人工全链复跑（2026-07-15，`eb47f45` 后，新码 server+daemon）

**结论：B-1 + B-5 两修复生产验证齐全，全链闭合。** 需求「番茄钟数据导出 CSV」→ Orch 拆解 **2 节点串行 repair0**（后端 CSV 接口 N1 → 前端按钮 N2，dep 边）→ Owner API 确认落地 →
- **B-1 ✅ 生产验证**：① `canvas_nodes.suggested_owner` 列在真库落值（两 dev 节点 = Dev-Claude-A，0013 迁移生效）；② N1(entry,无入边) 得「已落地」@mention → Dev **自主 claim+worktree+写码**；③ N2(downstream,有入边) 落地时**不 @**（延后）；④ **#8→done 瞬间**，#9 线程即现 **「上游已全部完成…建议认领：@Dev-Claude-A」**（task-done 触发面 → 读持久 suggested_owner → 发 `上游已全部完成` 前缀系统消息 system+mention→REMINDER）→ Dev **自主 claim #9**。**串行链无楔死**（B-1 死锁已根治）。
- **B-5 ✅ 生产验证（经 agent 端到端）**：Dev 用 `submit_task_contract` 提交 TaskHandoff——**#8 首投 `ok:false`(422 VALIDATION_FAILED)→ 按结构化错误自行修字段 → 重投 `ok:true`**（J8 式自愈循环经工具跑通）→ `set_task_status`→in_review **T7 门放行**（handoff 齐备）；#9 **首投即过**（同 session 学会）；连 Orch 汇总 #10 也用工具提交 handoff。三份 handoff 全由 agent 经工具提交，**零 REST 代劳**。
- **收尾链**：#8/#9→done → **merge 系统节点自动触发**主干 DAG 序 merge（`dc27ca7` #8 → `f956fcd` #9）→ **O8 汇总输入摘要（1/1 上游 Done）**→ Orch **总交付报告** → #10 in_review→done。终态：#8/#9/#10 全 done、canvas 全 done、主干干净。

**⚠️ 新观察 B-6[低，话术调优] Agent 交付收尾「冷启动」需一次示范**：Dev 首个任务(#8)写完码 + 发频道汇报后**径直 idle**（心智=「汇报完成→等待指示/自动推进」），**未自主走 handoff→in_review**；身份文案 `_IDENTITY_TEMPLATE` 的「交付纪律」一行不够显著，压不过「汇报后等待」惯性。**一次 nudge**（「请走正式收尾流程：提交 TaskHandoff 并置 in_review」，未点具体工具名）后，Dev 即自行发现并用 `submit_task_contract`，且**后续 #9 完全自主**闭环。判断：非能力缺口（B-5 工具可达且经 agent 能用已证），是**话术强度/冷启动**问题——建议把交付收尾流程写成更 prescriptive 的步骤清单（或 role 模板补「完成后必做三步」），使首个任务即自主收尾。本轮全链除三处合法人类触点（发需求/确认落地/评审批准 done）外，仅此一次 nudge = 「近零人工」。

## 0. 全链实证清单（本次真机全部走通的环节）

需求消息 → @Orch 唤醒 → **拆解**（3 节点/依赖分析/suggested_owner 结构化/**J8 修复循环**补 7 个 verify_ref rev.2 通过）→ awaiting_confirm → **浏览器草稿层确认落地** → 5 节点 4 边（+merge/O8 汇总自动追加）→「已落地」@建议人 →（B-1 死锁，绕过后）→ @mention 唤醒 → **claim_task 认领** → **worktree 自动派生**（真 git worktree add）→ **真实写码**（`recordFocusSession`/`getDailyStats`/统计页，3 commit）→ handoff 提交（B-5，REST 辅助）→ **T7/T4 门正确执法** → done → **gating 解锁下游** → 三节点滚动交付 → **merge 系统节点自动触发**：主干 `8cd0026→c19c37b→7beaa03` 严格 DAG 序 `--no-ff`、带 task/node 追溯 → **O8 汇总节点唤醒 Orch** → **总交付报告**（逐节点 commit→merge 追溯、AC 核对含诚实 ⏳ 待验标注）→ 遇 B-5 停止重试并精确升级 @Owner。终态：4 任务全 done、worktree 全终态、主干干净。

## 1. 上线前必修 Blocker

### B-1 [高] 投递前缀死锁：blocked 任务锚点楔死整个频道的 Agent 投递
**机制**（代码+实证双确认）：落地按节点序发锚点消息，串行链下 N2/N3 锚点属 blocked 任务 → `message_delivery_gated`=True（[canvas/service.py:243](../../apps/server/src/coagentia_server/canvas/service.py:243)）→ 投递「连续前缀」规则（[hub.py:1543-1545](../../apps/server/src/coagentia_server/computers/hub.py:1543)，#7 延迟不丢：首个 gated 即停）在第一条 gated 锚点截断 → **其后所有消息（含「已落地」@建议人唤醒）对全体 Agent 永不投递**。自锁闭环：N2 解锁需 N1 done ← N1 开工需唤醒 ← 唤醒需投递 ← 投递被 N2 锚点截断。
**实证**：三 Agent read_position 全部停在落地时刻；codex 积压 9 条逐条判定——首条（N2 锚点）gated=True 截断，其后 6 条（含「已落地」）gated=False 但永远出不去。**绕过**（read_position 拨过 3 条 gated 锚点）后投递立即恢复、全链继续；N1 done 后 N2 解锁、恢复路径正常。
**为何 sim 没抓到**：并行型拆解（入口节点全 unblocked）锚点不 gated 无截断；sim 场景多为并行图或经 REST 直接 claim 绕过唤醒。**串行链拆解必死锁**。
**波及面更广**：任何人在 blocked 任务线程发言，同样楔死全频道 Agent 投递直至解锁——不限于落地时刻。
**修复方向**（设计裁决，涉 M3b 裁决 2 + M6 #7 前缀规则的张力，建议 owner 拍板）：①落地消息序调整（「已落地」先于 blocked 锚点）只解落地窗口不解通例；②gating 收窄为「抑制唤醒但不扣投递」（premature-work 风险再评）；③前缀规则改每消息去重（契约 D deliver 帧语义变更）。
**✅ 已修复（2026-07-15，采纳 ②′）**：gating 改「跳过不截断」（blocked 内容仍不投=防抢跑保留，但不再截断前缀→死锁根除）+ 解锁主动唤醒（下游解 blocked 时发 @suggested_owner 系统消息，补 F2 姊妹缺口）。裁决与实施详 [`B1-DELIVERY-GATING-DECISION.md`](../B1-DELIVERY-GATING-DECISION.md)（含 §6 实施记录）。守门 pytest 1176/4（+3 回归测：realtest 串行链复刻/解锁唤醒幂等/守卫）。契约 D 帧零变更；`canvas_nodes.suggested_owner` 持久列（契约 A v1.0.13 + 迁移 0013）。

### B-5 [高] ✅ 已修（2026-07-15，`submit_task_contract` 工具）Agent 没有任何契约提交通道，交付收尾链在能力面断裂（4/4 Agent 命中）
**修复（owner 拍板：单工具 + body free-form + 话术/hint 同批）**：新增 MCP 工具 `submit_task_contract(task_id, kind, body)`（代理 POST /tasks/{id}/contracts，覆盖 task_plan+task_handoff，body free-form 由 server 按 kind 二次校验，VALIDATION_FAILED 逐字段 loc/msg 透传，J8 修复循环自愈）——契约 E v1.6（工具 16→17，Python-only 不入前端）+ daemon `mcp.py` 范式复制 trigger_deploy + 三处话术（`inject_contract_draft_request` 改指工具 / 身份基线 `_IDENTITY_TEMPLATE` 补交付纪律 / role 无 impl 模板故落基线）+ `HANDOFF_INCOMPLETE` 422 details 带 hint（set_task_status T7 门 + 升格门两处）。守门 pytest 1186/4·vitest 538·pyright0·ruff净·gen确定。**零迁移零新端点零新 WS 帧零前端**。**待做 = 零人工全链复跑真机验证工具贯通**（本批单元/StubHttp 覆盖）；改了 daemon 故复跑前须重启 server+daemon。以下为原始诊断——


**机制**：T7 门要求 TaskHandoff 才能置 in_review/done，但 16 个 MCP 工具**无契约提交工具**；系统注入的起草指令让 Agent「通过 POST /tasks/{task_id}/contracts 提交」（[hub.py:2444-2446](../../apps/server/src/coagentia_server/computers/hub.py:2444)）——Agent 够不着 REST。Agent 猜 `<control>` 信封发线程（无该解析面）→ set_task_status 422 只报「缺交接材料」无格式 hint → 死路。
**实证**：Dev-Claude-A 三个任务 + Orch-Main 汇总任务全部命中（4/4）；Orch 独立诊断出「此现象与 Dev 在 #3/#4/#5 一致」并停止重试升级 @Owner（失败姿态极佳）。本次测试以 REST 代提交（X-Acting-Member+Bearer）辅助推进，验证 T7/T4/handoff schema 校验全部正确。
**为何 sim 没抓到**：探针脚本以 REST 替 Agent 提交 handoff，从未经过 Agent 能力面。
**修复方向**：新增 MCP 工具 `submit_task_contract`（代理 POST /tasks/{id}/contracts）——契约 E 升版（工具 16→17）+ role_templates 补交付收尾话术 + `HANDOFF_INCOMPLETE` 422 建议携格式 hint（对齐 J8 修复循环的 hint 哲学）。工具扩面是 owner 既往亲自拍板项（M7 trigger_deploy 先例），落笔前请 owner 过目。

### B-4 [中] ✅ 已修（2026-07-15，`3abac15` 日志 + `1c68220` 修复）codex 适配器真机 turn 挂死（2/2 复现）
**现象**：codex Agent turn 启动（busy、能完成首个工具调用如 claim_task 200）随后**楔死**：无诊断、无文件改动、无消息、8+ 分钟。claude 对照正常。
**根因（新 daemon DEBUG 日志一次复现即锁定）**：codex app-server 单条 JSON-RPC 帧超过 **asyncio StreamReader 默认 64KB 行上限**时，`readline()` 抛 `LimitOverrunError('Separator is found, but chunk is longer than limit')` → 读循环死 → 待决 future 全 fail → agent 挂死无诊断。触发帧 = thread/resume 重放累积会话历史的响应（实测 98K token 上下文）、turn 内大工具输出/大 reasoning——**首个小帧正常、随后大帧哑火**，精确吻合「claim 200 后楔死」。claude stream-json 大工具结果同族风险（帧偏小侥幸未触发）。
**为何 sim 没抓到**：FakeAdapter 无真子进程/无 readline 缓冲面；且 daemon 此前对 daemon.log 近乎零写入，挂死时 server 侧诊断为空、无现场——**故先补 daemon 文件日志（可观测性前置）再复现**（本 finding 原「待查」即此路径）。
**修复**：① 补 daemon 进程级文件日志（`logconfig.py` + codex/claude 帧收发/stderr/生命周期 + client._log 迁移，env `COAGENTIA_DAEMON_LOG_LEVEL` 控级，帧原文 DEBUG）；② `_default_codex_spawn`/`_default_spawn` 给 `create_subprocess_exec` 传 `limit=32MB`（两 runtime 共用 `STREAM_LINE_LIMIT`）。
**真机验证（新码 daemon，DEBUG）**：之前必挂的 thread/resume 大帧响应现成功收下→握手 ready→idle；完整多工具 turn（list_members 200→send_message 201→final_answer→turn/completed 9.5s）端到端跑通、**0 LimitOverrunError**。守门 daemon pytest 214/4·ruff净·pyright(src)0。根因日志存档 `~/.coagentia/daemon/daemon.log.b4-rootcause`。

## 2. 次级发现与观察项

- **B-2 [中] DB status 与 daemon present 双源不一致**：`agents.status` 唯一写入方是 daemon 上报，但排查中 DB=idle 与实际投递可达性多次不符（挂死 turn 期间 busy 悬挂、restart 后语义混淆），曾误导根因判断走弯路。建议：暴露 `conn.present` 诊断端点；本次「busy 卡住」多数实为 B-4/B-5 的挂死 turn，非独立缺陷。
- **B-3 [低] onboarding 问候 × freshness 门**：上线问候被历史未读 held 扣住（设计内），首上线体验噪音；已关开关规避。
- **[观察·正面] 下游 worktree 基线含上游产出**：N2 分支基于 N1 的 commit（`2644f94→9a68f3a`），下游天然可见上游交付——DAG 序语义在 worktree 派生层的正确体现。
- **[观察] project.computer_id 绑旧机器时 worktree ensure 静默 no-op**（`conn is None: return False` 无诊断无升级）：机器换代/重建 computer 后所有旧 project 交付链静默断。建议至少落一条诊断。已用 PATCH 重绑修复。
- **[观察] 中断 turn 的 drafting 提案悬挂**：中文那次被我 restart 打断的提案停在 drafting（另有一个历史悬挂），无超时回收面（24h 提醒是纯推导不改状态）。清理项。
- **[正面清单]** 本次真机验证全部正确的护栏：T7（HANDOFF_INCOMPLETE）/T4（非法流转 422 携 allowed）/handoff schema 逐字段 422/claim 防重 409 CLAIM_RACE/O4 建议不锁定（codex 建议、claude 认领合法）/J8 修复循环 hint 自愈/O8 报告诚实标注 ⏳/Agent 失败姿态（停止重试+升级人类）。

## 3. 澄清（排查中证伪的假设，勿重复怀疑）

- ~~「拆解没落 owner」~~：设计裁决 O4「建议不锁定」，`suggested_owner` 已结构化填写、「已落地」正确 @建议人。
- ~~「实现者 present 卡 STARTING」~~：presence 正常（投递恢复后 read_position 即推进）；真根因是 B-1 前缀截断。
- ~~「中文 MCP 编码挂起（CR-M8-2 复发）」~~：中英文对照两次拆解全走通，Orch 中文报文完好；B-4 的 codex 挂死与载荷语言无关。

## 4. 下一步建议（优先级序）

1. ~~**B-1 修复裁决**（owner 拍板方向）→ 修后回归~~ **✅ 完成（②′，2026-07-15）**：单元+回归守门绿；**待做 = 修 B-5 后零人工辅助全链复跑**验证解锁唤醒真机贯通（本批仅单元/StubDaemon 覆盖，真 CLI 全链复跑归下一步）。
2. **B-5 工具扩面**（owner 拍板 E v1.x 升版）→ 修后从头跑一次零人工辅助的全链。
3. **daemon 文件日志** → 复现 B-4 codex 挂死定位。
4. 以上收敛后按 [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) 铺开 T-D/T-G/T-B/T-C/T-E/T-H/T-F′ 与 6 窗口规模（当前发现不修复，多窗口场景会大面积踩 B-1/B-5）。

## 5. 复现锚点

- 库：`~/.coagentia/server/coagentia.db`（0012；realtest 频道全链终态可考古）；备份 ×2。
- computer=RealTest-PC key=`cak_01kxhxqa0hvzbkr0f4d8hgpyfa`；channel=`01KXHXW27P1P1D5G94H1V5HA04`；proposal=`01KXHYWQ7FEHNP5C2P2ZRB6VV0`；任务 #3/#4/#5/#6 全 done。
- 主干证据：scratch-pomodoro `git log`：`7beaa03/c19c37b/8cd0026`（DAG 序 merge）← `446b2e5/2644f94/9a68f3a`（实码）。
- 代码锚点：hub.py `_deliver_message`(1478)/`_filter_agent_delivery`(1540)/`_compute_trigger`(1605)/`inject_contract_draft_request`(2434)；canvas/service.py `message_delivery_gated`(243)；worktrees/service.py `ensure_plans`(83)/`delivery_waits_for_directory`(576)。
