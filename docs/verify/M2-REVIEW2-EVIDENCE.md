# M2 二轮实机 verify + review 修复批证据

> 2026-07-10。两段：① M2 收口态（`6c12b90`）复验；② `/code-review high` 修复批实机复核。
> 隔离验证服务 = 临时 DB（alembic + seed）+ uvicorn 127.0.0.1:8801 同源托管真实 dist。

## 1. M2 收口态复验（修复批之前）——35/35 PASS

- 番茄钟主流程真 HTTP 10 步全过（As Task 原子建任务 → Agent claim 联动 → 线程汇报 → 文件交付 → in_review → done → TaskDetail/文件页签/task #n 引用）。
- 对抗 probe 10 项全过：done 终态双守卫、**并发 claim 恰一成功**（[200,409] + CLAIM_RACE current_owner）、非法边、同态幂等、非 owner unclaim 403、assign 不动 status、convert 幂等、DM/线程拒建、鉴权冒充双 403。
- 聚合面 + WS：FTS «» 高亮、mention/dm 仅人类、activity done 幂等 + unread -1、文件游标翻页、交付物字节一致、四事件族广播齐全。
- 浏览器：UI As Task 无刷新出牌、看板 WS 移列、合法边下拉防呆、Ctrl+K 三分组高亮跳转、Mark as done 徽标即时消失、inline 预览 + SHA-256；console 零错误。
- 截图：`v2-p1-channel-real.png` / `v2-p3-board-after-claim-ws.png` / `v2-p5-legal-transitions.png` / `v2-p10-search-fts.png` / `v2-p9-activity.png` / `v2-p4-files-preview.png`。

## 2. `/code-review high`（7 finder × 34 候选 × 逐项验证）

15 CONFIRMED / 1 PLAUSIBLE / 1 REFUTED；前 10 上报并**全部修复**，明细见 CURRENT-HANDOFF 增补。

## 3. 修复批实机复核 —— 全过

| 修复 | 复核方式 | 观察 |
| --- | --- | --- |
| activity done 归属门 | Agent Bearer POST 他人条目 | **404**，done_at 不动；Owner 本人 200（回归测试 ×2 并入套件） |
| ActivityItemPublic.actor | GET /activity | `actor_member_id`=Pat、`member_id`=Owner 两字段并存 |
| Activity 行为人文案 | 浏览器 P9 | 「**Pat** 在 #build 提及了你」（修复前错显 Memcyo）→ [v3-p9-activity-actor-fixed.png](v3-p9-activity-actor-fixed.png) |
| 时间显示 UTC→本地 | 浏览器 P1 | seed 消息 04:12(UTC 原样) → **21:12(本地)**，日期分隔同步本地化；Activity/消息流同刻同显 |
| 线程面板附件卡 | 浏览器 P5 | 线程内交付 deliver.md 渲染附件卡（25 B + 下载链接），此前结构性缺失 |
| 认领钮占用置灰 | 浏览器 P5 | in_progress·owner=Pat 的任务，Owner 视角认领钮 **disabled**（修复前可点必吃 409） |
| tasks 分页聚合 | 代码 + 套件 | api.tasks 循环 next_cursor 全量拉取（limit 200/页，40 页护栏） |
| 其余（IME/Ctrl+Enter、Firefox setData、onLocate 定位闪烁、usage 冻结失效、wsBridge channelFiles/成员过滤、UTC 硬切 ×5 处收拢 lib/time.ts） | typecheck/vitest/build + 代码走查 | 全绿 |

## 4. 收口守门

后端 `pytest -q` = **387 passed, 3 skipped**（+3 回归：done 归属 ×2、actor 断言、Public 形状放宽单测）；ruff 干净；`pnpm gen` 确定性（contracts-ts 含 actor 字段重生成）；前端 typecheck/build 绿、vitest 18 passed。契约 A 升 **v1.0.3**（Public 派生字段登记）。
