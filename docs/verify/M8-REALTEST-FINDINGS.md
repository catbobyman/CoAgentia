# M8 上线前实机测试 — 第一阶段发现与全链实证（真 CLI 多 Agent）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-14~15（真机会话，Owner 授权自主推进） |
| 环境 | 真 uvicorn server(8787) + 真 web(5173) + 真 daemon + 真 claude(2.1.208)/codex(0.144.0) CLI；默认库迁移 0011→0012（备份 `coagentia.pre-m8-migrate.*.db` / `coagentia.pre-surgery.db`） |
| 定位 | 首次以**真 CLI**（非 daemon-sim FakeAdapter）跑完「需求→拆解→落地→交付→合并→O8 汇总」全链。历届 M6/M7/M8 verify 全用 FakeAdapter；本次填补真机空白，方案 = [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) T-A′ 变体 |
| 一句话结论 | **全链在真 CLI 下端到端贯通**（两处需人工辅助绕过）：交付链闭环到主干 3 个 DAG 序 merge commit + O8 总报告。发现 2 个上线前必修 blocker（投递前缀死锁 B-1、Agent 无契约提交通道 B-5）+ 1 个 codex 适配器挂死（B-4），全部为 sim 掩盖的真机专属缺陷。 |

## 0. 全链实证清单（本次真机全部走通的环节）

需求消息 → @Orch 唤醒 → **拆解**（3 节点/依赖分析/suggested_owner 结构化/**J8 修复循环**补 7 个 verify_ref rev.2 通过）→ awaiting_confirm → **浏览器草稿层确认落地** → 5 节点 4 边（+merge/O8 汇总自动追加）→「已落地」@建议人 →（B-1 死锁，绕过后）→ @mention 唤醒 → **claim_task 认领** → **worktree 自动派生**（真 git worktree add）→ **真实写码**（`recordFocusSession`/`getDailyStats`/统计页，3 commit）→ handoff 提交（B-5，REST 辅助）→ **T7/T4 门正确执法** → done → **gating 解锁下游** → 三节点滚动交付 → **merge 系统节点自动触发**：主干 `8cd0026→c19c37b→7beaa03` 严格 DAG 序 `--no-ff`、带 task/node 追溯 → **O8 汇总节点唤醒 Orch** → **总交付报告**（逐节点 commit→merge 追溯、AC 核对含诚实 ⏳ 待验标注）→ 遇 B-5 停止重试并精确升级 @Owner。终态：4 任务全 done、worktree 全终态、主干干净。

## 1. 上线前必修 Blocker

### B-1 [高] 投递前缀死锁：blocked 任务锚点楔死整个频道的 Agent 投递
**机制**（代码+实证双确认）：落地按节点序发锚点消息，串行链下 N2/N3 锚点属 blocked 任务 → `message_delivery_gated`=True（[canvas/service.py:243](../../apps/server/src/coagentia_server/canvas/service.py:243)）→ 投递「连续前缀」规则（[hub.py:1543-1545](../../apps/server/src/coagentia_server/computers/hub.py:1543)，#7 延迟不丢：首个 gated 即停）在第一条 gated 锚点截断 → **其后所有消息（含「已落地」@建议人唤醒）对全体 Agent 永不投递**。自锁闭环：N2 解锁需 N1 done ← N1 开工需唤醒 ← 唤醒需投递 ← 投递被 N2 锚点截断。
**实证**：三 Agent read_position 全部停在落地时刻；codex 积压 9 条逐条判定——首条（N2 锚点）gated=True 截断，其后 6 条（含「已落地」）gated=False 但永远出不去。**绕过**（read_position 拨过 3 条 gated 锚点）后投递立即恢复、全链继续；N1 done 后 N2 解锁、恢复路径正常。
**为何 sim 没抓到**：并行型拆解（入口节点全 unblocked）锚点不 gated 无截断；sim 场景多为并行图或经 REST 直接 claim 绕过唤醒。**串行链拆解必死锁**。
**波及面更广**：任何人在 blocked 任务线程发言，同样楔死全频道 Agent 投递直至解锁——不限于落地时刻。
**修复方向**（设计裁决，涉 M3b 裁决 2 + M6 #7 前缀规则的张力，建议 owner 拍板）：①落地消息序调整（「已落地」先于 blocked 锚点）只解落地窗口不解通例；②gating 收窄为「抑制唤醒但不扣投递」（premature-work 风险再评）；③前缀规则改每消息去重（契约 D deliver 帧语义变更）。

### B-5 [高] Agent 没有任何契约提交通道，交付收尾链在能力面断裂（4/4 Agent 命中）
**机制**：T7 门要求 TaskHandoff 才能置 in_review/done，但 16 个 MCP 工具**无契约提交工具**；系统注入的起草指令让 Agent「通过 POST /tasks/{task_id}/contracts 提交」（[hub.py:2444-2446](../../apps/server/src/coagentia_server/computers/hub.py:2444)）——Agent 够不着 REST。Agent 猜 `<control>` 信封发线程（无该解析面）→ set_task_status 422 只报「缺交接材料」无格式 hint → 死路。
**实证**：Dev-Claude-A 三个任务 + Orch-Main 汇总任务全部命中（4/4）；Orch 独立诊断出「此现象与 Dev 在 #3/#4/#5 一致」并停止重试升级 @Owner（失败姿态极佳）。本次测试以 REST 代提交（X-Acting-Member+Bearer）辅助推进，验证 T7/T4/handoff schema 校验全部正确。
**为何 sim 没抓到**：探针脚本以 REST 替 Agent 提交 handoff，从未经过 Agent 能力面。
**修复方向**：新增 MCP 工具 `submit_task_contract`（代理 POST /tasks/{id}/contracts）——契约 E 升版（工具 16→17）+ role_templates 补交付收尾话术 + `HANDOFF_INCOMPLETE` 422 建议携格式 hint（对齐 J8 修复循环的 hint 哲学）。工具扩面是 owner 既往亲自拍板项（M7 trigger_deploy 先例），落笔前请 owner 过目。

### B-4 [中] codex 适配器真机 turn 挂死（2/2 复现）
**现象**：codex Agent 被唤醒后 turn 启动（status busy、能完成首个工具调用如 claim_task 200），随后**楔死**：无诊断、无文件改动、无消息，8+ 分钟（CPU 有消耗）。两次复现（一次消息响应、一次 N2 认领后）。claude 适配器同场景全程正常（对照）。
**待查**（需 daemon 可读日志）：codex app-server JSON-RPC 事件流在首个工具调用后是否停帧；与 CR-M8-2 家族（MCP stdio）或 E2 相位聚合的关系。**障碍**：daemon 日志不写 stdout，运行时不可观测——建议先加 daemon 文件日志再复现。

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

1. **B-1 修复裁决**（owner 拍板方向）→ 修后用本库 realtest 频道串行链场景回归。
2. **B-5 工具扩面**（owner 拍板 E v1.x 升版）→ 修后从头跑一次零人工辅助的全链。
3. **daemon 文件日志** → 复现 B-4 codex 挂死定位。
4. 以上收敛后按 [M8-REALTEST-PLAN.md](M8-REALTEST-PLAN.md) 铺开 T-D/T-G/T-B/T-C/T-E/T-H/T-F′ 与 6 窗口规模（当前发现不修复，多窗口场景会大面积踩 B-1/B-5）。

## 5. 复现锚点

- 库：`~/.coagentia/server/coagentia.db`（0012；realtest 频道全链终态可考古）；备份 ×2。
- computer=RealTest-PC key=`cak_01kxhxqa0hvzbkr0f4d8hgpyfa`；channel=`01KXHXW27P1P1D5G94H1V5HA04`；proposal=`01KXHYWQ7FEHNP5C2P2ZRB6VV0`；任务 #3/#4/#5/#6 全 done。
- 主干证据：scratch-pomodoro `git log`：`7beaa03/c19c37b/8cd0026`（DAG 序 merge）← `446b2e5/2644f94/9a68f3a`（实码）。
- 代码锚点：hub.py `_deliver_message`(1478)/`_filter_agent_delivery`(1540)/`_compute_trigger`(1605)/`inject_contract_draft_request`(2434)；canvas/service.py `message_delivery_gated`(243)；worktrees/service.py `ensure_plans`(83)/`delivery_waits_for_directory`(576)。
