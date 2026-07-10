# M4a 实机 verify 证据（沉默提醒与循环 Reminder）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-10 |
| 范围 | 块 **M4a**（F0 契约登记 / F1 0006 建表 / F2 挂账双修 / F3 D5 沉默升级链 / F4 循环 Reminder+LoopContract / B-M4-1 前端）——freshness/HeldDraft 属块 M4b，不在本次 |
| 出口对应 | PRD §8 M4 行后半句「制造一次沉默任务：提醒与升级触达」+ D5 升级链 + D1-L2 循环 Reminder 解锁 |
| 结论 | **探针 16/16 PASS + 浏览器 3 截图 + console 0 错误** |

## 1. 隔离环境

- 真 uvicorn（`create_app`）监听 `127.0.0.1:8799`，同源托管 `apps/web/dist`。
- 隔离临时库 `m4a_verify.db`：`COAGENTIA_ALEMBIC_URL` → `alembic upgrade head`（含 0006_m4）→ `seed_database`。
- hub 扫描间隔调小（`silence_interval=2s`、`reminder_interval=2s`）确定性触发。
- 注入已知测试 key 到 seed computer（供 Agent 主体 REST：`POST /reminders`）。
- 启动器/探针脚本：scratchpad `m4a_server.py` / `m4a_probe.py`（真 HTTP 驱动 + 直连 sqlite 做确定性核对；`messages` 不可变触发器阻止回退，改用频道阈值=0h 驱动超期）。

## 2. 探针结果（16/16 PASS）

### 场景 A —— 沉默提醒与升级链（D5）

| # | 断言 | 结果 |
| --- | --- | --- |
| A0 | claim → in_progress，owner=Pat | PASS |
| A1 | PATCH /channels 阈值=0h + remind_escalation 开 | PASS |
| A2 | 阈值=0h 驱动超期 | PASS |
| A3 | 任务A（todo）触发 `reminder_sent` | PASS（events=`[reminder_sent]`） |
| A4 | 任务B（in_progress）触发 `reminder_sent` | PASS（events=`[claim, status_change, reminder_sent]`） |
| A5 | 任务A 升级 `escalated` | PASS（events=`[reminder_sent, escalated]`） |
| A6 | 提醒系统消息落任务**锚点线程** | PASS |
| A7 | 任务A（todo）提醒 @创建者(owner) 写 `message_mentions` | PASS |
| A8 | 升级产出 Activity `silence_escalation`（置顶区） | PASS |
| A9 | **升级后静默**：`escalated` 不重复（等 3 扫描周期仍 =1） | PASS（before=1 after=1） |

**防自激验证**：A9 证明 `escalated` 写入后未刷新 `last_activity`（否则链条自我重置、永不静默）——F3 的 `SELF_EXCITE_EVENT_KINDS` 排除生效。

### 场景 B —— 循环 Reminder + LoopContract（D1-L2 解锁）

| # | 断言 | 结果 |
| --- | --- | --- |
| B1 | recurring 无 loop_contract → 422（rule=D1-L2） | PASS |
| B2 | once 携带 loop_contract → 422 | PASS |
| B3 | recurring + loop_contract → 201 + `loop_contract_id` 回填 | PASS（lcid=01KX6WYXVYFWWCCW1JS25NNV0X） |
| B4 | `task_contracts` 挂接行（kind=loop_contract, reminder_id 挂接, XOR task_id NULL） | PASS |
| B5 | GET /agents/{Pat}/reminders 见 recurring active | PASS |
| B6 | 触发后**保持 active** + `next_fire_at` 前进一个 interval | PASS（`20:54:30Z` → `21:54:30Z`，精确 +PT1H） |

## 3. 浏览器截图（同源 8799，console 0 错误）

| 截图 | 证据 |
| --- | --- |
| [m4a-build-silence-stream.png](m4a-build-silence-stream.png) | #build 主流完整 D5 链：沉默提醒→@Memcyo（任务A todo=创建者）/ @Pat（任务B in_progress=owner）；沉默升级→@Memcyo（人类成员）；末尾「系统提醒触发（reminder …）」= 场景 B recurring 触发 |
| [m4a-activity-silence-escalation.png](m4a-activity-silence-escalation.png) | Activity「需要处理」置顶区渲染真 `silence_escalation` 条目（「有人 的任务长时间静默,已升级」#build）——B-M4-1 Activity 置顶接真 |
| [m4a-reminders-recurring.png](m4a-reminders-recurring.png) | Agent Pat · Reminders 页签：`RECURRING · PT1H · 下次 … · 锚点`，`循环 · 契约` 角标（loop_contract 挂接）、`active` 徽标、取消按钮——B-M4-1 强化 |

@ 目标全部正确：todo→创建者、in_progress→owner、升级→人类成员。console 无 error。

## 4. 守门基线（M4a 收口，全绿）

- 后端 `uv run pytest -q`：**539 passed / 3 skipped**（M3 基线 483 → +56）
- web `vitest`：**89 passed**（基线 76 → +13）
- `pnpm typecheck`：pyright **0** + 双 tsc Done
- `uv run ruff check .`：All checks passed
- `pnpm gen`：确定性（二跑逐字节一致）
- `pnpm -F @coagentia/web build`：绿

## 5. `/code-review high` 结论与修复（8 角度 × 25 agents → 10 CONFIRMED）

10 条 CONFIRMED 收敛为「1 个正确性 bug + 一簇 hub 清理/效率」，另 2 条 PLAUSIBLE。全部处理：

| # | 类别 | 发现 | 处置 |
| --- | --- | --- | --- |
| 1 | **正确性（必修）** | recurring reminder 重排只从旧 next_fire_at +1 interval：① 建即触发（创建时 next_fire_at=now）；② 停机漏 K 周期后每轮扫描逐格重放，洪泛 K 条系统消息 + K 次 agent wake | `interval.next_after`（O(1) 塌缩到 >now 的下一网格点）替代逐格；创建 recurring 时 next_fire_at=now+interval（建后一周期才首触发）。新增 test_daemon 塌缩+不重放 + test_interval next_after 3 例 |
| 2 | 复用/简化/altitude（3 条合一） | 系统消息发射骨架（insert _MSG + mention + 回读 + emit）在 run_reminder_scan/_emit_silence_reminder/_emit_silence_escalation 三处拷贝 | 抽 `_post_system_message` helper 三处共用 |
| 3 | 简化+效率（2 条合一） | _silence_inputs 对同一 task_events 连发 3 条 func.max（只差 kind） | 合并为单查询条件聚合（case-when 三 max 一趟） |
| 4 | 效率 | run_silence_scan 逐任务 SELECT channel（N+1） | 按 distinct channel_id 一次 IN 批取建 dict |
| 5 | 效率 | run_reminder_scan 触发后回读 reminder 行拼 REMINDER_UPDATED | 由内存行 `{**reminder, ...}` 拼出免回读 |
| — | 效率（顺手，PLAUSIBLE） | interval._format_iso 逐字复制 now_iso 格式串 | 抽 `ledger.service.format_iso(dt)` 单源，now_iso 与 interval 同调 |
| — | 健壮性（PLAUSIBLE，**评估后不采纳**） | run_silence_scan 单事务无逐任务隔离，一任务失败拖垮整轮 | 试加 per-task SAVEPOINT 后**回退**：tx.emit 提交后 flush、与 savepoint 解耦，部分失败会广播「幽灵事件」（回滚的写却发了 MESSAGE_CREATED）——单事务原子 + 下轮重试更安全，引用目标已 removed_at 过滤、持久失败极不可能 |

守门复跑全绿：后端 **542 passed / 3 skipped**（+3 next_after）、pyright 0、ruff 干净；前端/gen 未触（本批纯 server Python）。

## 6. 未覆盖（属块 M4b，非本次范围）

freshness 门 / HeldDraft 卡片三键 / G4 定时自动重评估 / G5 held 升级 —— 块 M4b。本次仅证 M4a（D5 沉默 + 循环 Reminder）。
