# M4b 实机 verify 证据（freshness 与 HeldDraft）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-10 |
| 范围 | 块 **M4b**（F5 freshness 门 + held 三键端点 / F6 G4 定时自动重评估 + G5 升级 + GC 豁免 / B-M4-2 HeldDraft 卡片 + wsBridge）——**收口即 PRD M4 出口** |
| 出口对应 | PRD §8 M4 行前半句「制造一次 held 场景：卡片可见、放行 1 分钟内交付」+ G3 三键 + G4 超时自愈 + G5 升级喊人 + 死循环结构性破除 |
| 结论 | **探针 38/38 PASS + 浏览器 3 截图（held 卡/升级/Activity 置顶）+ console 0 错误** |

## 1. 隔离环境

- 真 uvicorn（`create_app`）监听 `127.0.0.1:8801`，同源托管 `apps/web/dist`。
- 隔离临时库 `m4b_verify.db`：`COAGENTIA_ALEMBIC_URL` → `alembic upgrade head`（含 0006_m4）→ `seed_database`。
- **真 websockets daemon-sim**（非 in-process StubDaemon）连 `/api/daemon/ws`，hello 报 Pat/Hank/Rin/Orchestrator 全 idle，收指令即 ack `done`、后台 ping 保活；**浏览器 WS** 连 `/api/ws` 录广播事件。
- `hub.held_interval=2s`（默认 5.0）确定性触发 G4；注入已知测试 key 到 seed computer（Agent 主体 REST）。
- 启动器/探针：scratchpad `m4b_server.py` / `m4b_probe.py`（全 REST 走 `asyncio.to_thread`——reevaluate/discard 服务端 `_run_sync` 同步等 daemon-sim ack，若阻塞事件循环则 reader 无法 ack → 死锁；直连 sqlite 只读做确定性核对）。

## 2. 探针结果（38/38 PASS）

### 场景 H —— held + release + 1 分钟内交付（PRD M4 出口）

| # | 断言 | 结果 |
| --- | --- | --- |
| H1 | Pat（Agent 主体）发消息、目标 scope 有未读 → **202**（不落库） | PASS |
| H2 | held_draft 载草稿全文 | PASS |
| H3 | reasons：total_unread=3 + 未读 id 清单（可点跳转） | PASS |
| H4 | held 行落库 status=held | PASS |
| H5 | guard.held 诊断写入 | PASS |
| H6 | WS `held_draft.created` 广播（无刷新） | PASS |
| H7–H9 | 人类 release → 200，响应 message.author=Pat，held→released 终态 | PASS |
| H10 | 放行后消息落库（+1） | PASS |
| **H11** | **放行 1 分钟内 `message.deliver` 送达 Rin（含放行消息）** | **PASS** |
| H12 | WS `held_draft.updated`（released 反流） | PASS |

### 场景 D —— discard（G3）

| # | 断言 | 结果 |
| --- | --- | --- |
| D1 | 再次被扣 → 202（前行已终态 → 新活动行） | PASS |
| D2–D3 | 人类 discard → 200，held→discarded 终态 | PASS |
| D4 | discard 直投 `message.inject`(guard_feedback) 告知 Pat | PASS |
| D5 | guard.discarded 诊断写入 | PASS |

### 场景 R —— reevaluate + 死循环破除（裁决 4/10）

| # | 断言 | 结果 |
| --- | --- | --- |
| R1–R3 | 被扣 → 人类 reevaluate → 200，held→reevaluating | PASS |
| R4 | 重评估组合 = **wake + deliver + inject 三帧**（daemon-sim 收齐） | PASS |
| **R5** | **deliver ack 推进 Pat read_position**（None → `01KX7MZSMAFJ...`；防复扣关键） | **PASS** |
| R6 | guard.reevaluate_requested 诊断（人类触发） | PASS |
| **R7** | **推进游标后 Pat 重发同稿 → 201 过门（死循环结构性破除）** | **PASS** |

> R5+R7 = 裁决 4「inject 不动游标→仅直投必然复扣死循环」的正面证明：唯有 deliver 推进 read_position 才使重发过门。

### 场景 G4 —— 定时自动重评估（held_reeval_min=0 立即到点）

| # | 断言 | 结果 |
| --- | --- | --- |
| G4-0/1 | PATCH held_reeval_min=0 → 新扣 next_reeval_at≈now → 202 | PASS |
| G4-2 | hub `run_held_scan`（held_interval=2s）**自动置 reevaluating** | PASS |
| G4-3/4 | 自动触发写 guard.reevaluate_requested，payload `resolved_by=None`（区别人类触发） | PASS |
| G4-5 | 在线组合投递给 Pat | PASS |

### 场景 G5 —— 连扣升级喊人 + 停自动（held_escalate_n=1）

| # | 断言 | 结果 |
| --- | --- | --- |
| G5-0/1 | PATCH held_escalate_n=1 → 首扣即达阈值 → 202 | PASS |
| G5-2 | held_count=1 达阈值 → **escalated_at 非空**（升级） | PASS |
| G5-3 | guard.escalated 诊断写入 | PASS |
| G5-4 | held_escalation activity 产出（member=Memcyo 人类，置顶区） | PASS |
| G5-5 | scope 系统消息喊人（频道主流「护栏升级…需人类介入」） | PASS |
| G5-6 | 升级系统消息 @Memcyo（人类成员） | PASS |
| **G5-7** | **升级后停自动重评估**（强制 next_reeval_at 到点、等 2 个 G4 周期，escalated_at 排除 → 状态仍 held） | **PASS** |

## 3. 浏览器截图（同源 8801，1440×900，console 0 错误）

| 截图 | 证据 |
| --- | --- |
| [m4b-held-card-build.png](m4b-held-card-build.png) | #build 活动 held 卡：`被扣草稿 @Pat` + 草稿全文 + `因这些未读被扣 msg 1CNZSB`（可点跳转）+ 倒计时（实测由 `1:28 后重评估`→`0:36 后重评估`逐秒递减，本地读秒不推帧；5 分钟到点后 G4 翻 `重评估中…`）+ **放行/重评估/丢弃三键**（afterglow DS 橙色强调框） |
| [m4b-research-escalation.png](m4b-research-escalation.png) | #research 完整 held 生命周期：`已放行·草稿已作为消息发出·by Memcyo`（H 终态回执）/ `已丢弃·草稿不再发送`（D/R/G4 终态回执）/ 末卡红色 **`⚠ 已升级喊人`** 横条（G5 escalated_at 非空）+ 草稿全文 + 未读原因；升级系统消息「护栏升级：Agent『Pat』的草稿已连续被扣 1 次仍待处理，需人类介入：@Memcyo」在主流 |
| [m4b-activity-held-escalation.png](m4b-activity-held-escalation.png) | Activity「─ 需要处理 ─」置顶区渲染真 `held_escalation` 条目（盾牌图标 +「有人 的草稿反复被扣,已升级 · #research · 20:56」）——B-M4-2 Activity 接真 |

三键、倒计时、终态回执、升级横条、Activity 置顶全部真数据渲染；WS `held_draft.created/updated` 无刷新更新。

## 4. 守门基线（M4b 收口态，含 code-review 修复，全绿）

- 后端 `uv run pytest -q`：**572 passed / 3 skipped**（M4a 基线 542 → +30；含 review 新增 3：list 默认活动态 / 幂等前置 / reevaluate 终态守卫）
- web `vitest`：**106 passed**（M4a 基线 89 → +17：HeldDraftCard 11 + wsBridge.heldDraft 6）
- `pnpm typecheck`：pyright **0** + 双 tsc Done
- `uv run ruff check .`：All checks passed
- `pnpm gen`：确定性（packages/ 零漂移，契约未被改）
- `pnpm -F @coagentia/web build`：绿

## 5. `/code-review high` 结论与修复（8 角度 × ≤6 候选 → 对抗性 verify → 17 CONFIRMED + 2 PLAUSIBLE）

收敛处置：**5 个正确性/健壮性修复 + 1 簇清理 + 2 项 DRY 债挂账**。全部守门复跑绿（上述 §4）。

| # | 类别 | 发现 | 处置 |
| --- | --- | --- | --- |
| #4 | **正确性（必修）** | freshness 门在幂等检查之前 → 已登记首次结果的重放遇期间新未读被误扣成 202 held，人类放行产生**重复消息**（违 §1） | `ledger.service.lookup` 只读探账本，门前查 hit/mismatch（回原 M1），absent 时落库路径才 record()（避 held 时留悬挂账本行）。新增 test_idempotent_replay_returns_first_result_over_hold |
| #5 | **正确性/并发** | reevaluate_held 的 UPDATE 无终态守卫 → 与并发 discard 竞态可复活已丢弃草稿（status=reevaluating 却 resolution=discarded） | UPDATE 限活动态（status IN held/reevaluating）+ rowcount==0 → `HeldDraftResolved` → 路由 409。新增 test_reevaluate_guard_rejects_concurrently_resolved |
| #6 | **正确性** | G4 对离线 Agent 也提交 reevaluating，但扫描只选 status='held' → 行永卡 reevaluating、对账无 held 感知永不恢复 | run_held_scan **在线先探再翻状态**（离线留 held 下轮重试，对齐 reevaluate_held 离线先探范式）。改 test_held_scan_offline_leaves_held_for_retry |
| #1 | 正确性 | heldDrafts 清单不带 status → server 回全量，keyset 升序把老终态填满首页、活动 held 挤到后页 | server list_held_drafts status 省略 → 默认只回活动态；`ACTIVE_STATUSES` 提公开单源（gc.py 复用消两份重复元组）。新增 test_list_defaults_to_active_excludes_terminal |
| #3 | 健壮性 | discard/reevaluate 的 503（daemon 离线）被吞、无用户反馈 | useHeldDraftAction onError 非-409 弹 error toast。新增 vitest「503 → error toast」 |
| #7/#10 | 复用/简化 | reevaluate_held 内联 insert(_DIAG)+字面量 vs run_held_scan 用 helper+常量 | 统一走 guard_service.write_guard_diagnostic + GUARD_REEVALUATE_REQUESTED |
| #2 | 资源（评估：#6 覆盖核心） | reevaluating 无自动终态 → GC 豁免泄漏 | 核心「离线卡 reevaluating」由 #6 消除；在线残余（resend 后 reevaluating 挂起）为活动草稿保留附件的既定设计（人类可释/弃），未额外改 |
| #8/#9 | 复用（挂账） | `_post_system_message`（hub/guard/channels 三+份）/ `_channel_human_members`（hub/guard 两份）跨模块重复 | **挂账**：确认 DRY 债非 bug；消除需新建共享模块并触及承重 reminder/silence 路径，收口期 ROI 低——见 M4-HANDOFF §8 |

## 6. 观察（非阻塞）

- 升级态 held 行若 next_reeval_at 已过（本探针 G5-7 人为强制过期以证停自动），卡片倒计时组件显示「重评估中…」而实际 status=held——cosmetic 显示与真实态的边角不一致（正常非升级场景 G4 会 2s 内真翻转 reevaluating，此显示才准）。挂账观察项。
- 浏览器 pane（in-app Claude_Browser）截图在本环境超时（DOM/交互经 read_page/get_page_text/click 全正常，纯截图基础设施问题）；PNG 改由 playwright 独立 chromium 采集。
