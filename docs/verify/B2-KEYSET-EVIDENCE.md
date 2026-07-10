# 挂账批2 实机证据：keyset 分页统一整改（messages/tasks/files/activity）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-10 |
| 出处 | M2 挂账 3 + 二轮 review 挂账（M3-HANDOFF §8）：`after` id 游标按"结果集内位置"定位，行离开过滤集时静默从头翻重发首页；activity/files 无 SQL LIMIT 全量材料化；ActivityScreen 三档缓存双请求 |
| 修法 | `_pagination.keyset_page`：游标仍是裸 id（对外形状不变），服务端按 PK 回查游标行取 `(created_at, id)` 做 SQL 行值比较（SQLite row-value；排序保持复合键不改裸 id，避开 seed 回填时间与 ULID 序不一致边角）；`LIMIT limit+1` 下推；四端点统一接入（messages 内联/tasks 内联/files·activity 原 `cursor_page` 全废）。messages `before` 修成真**紧邻窗口回翻**（旧实现错误返回频道头部窗口）。前端 ActivityScreen 改 `'all'` 单拉 + tab 客户端过滤（口径对齐服务端 filter 语义；徽标只数未读、Mentions 列表含已 done 灰显=有意设计不改），wsBridge 删 unread/mentions 多档 patch |
| 方式 | 真 uvicorn（端口 8803，临时库 head+seed）+ 真 HTTP（httpx，Agent 提及经注入测试 key 双头）+ 浏览器同源（playwright） |

## A. 真 HTTP probe：10/10 PASS（scratchpad b2_probe.py + activity 补跑）

1. PASS — tasks 首页 keyset（limit=1，next_cursor=首行 id）
2. PASS — **after 行经 claim 离开 status=todo 过滤集后，续翻返回 b2-t1 不重发首页**（旧实现即挂账 3 触发点）
3. PASS — tasks 游标链走到尾（b2-t2 + next_cursor=None）
4. PASS — messages `before` 紧邻窗口（返回 b2m-3/4，非频道头部）
5. PASS — `before` 续翻拿更旧窗口（b2m-1/2，next_cursor=窗口最旧 id）
6. PASS — files 倒序 keyset 两页无重叠且最新在前
7. PASS — activity keyset 两页无重叠（Agent Hank 三次 @Owner 生成，Bearer+X-Acting-Member）
8. PASS — activity filter=unread 与游标可组合（LIMIT 下推）
9. PASS — unread 档游标续翻无重叠
10. PASS — 未知游标宽容：200 从头翻（沿旧行为，不 404/500）

## B. 浏览器实证（b2-activity-single-fetch.png）

`/activity`：performance 资源条目仅 **1 次** `/api/activity?filter=all`；徽标 Unread 3 / Mentions 3；
点击 Mentions tab 后 **requestsAfter == requestsBefore == 1**（零新请求，纯客户端过滤），
3 行 mention 行为人正确显示 Hank（actor_member_id 派生字段）。

## C. 守门基线

- 后端 `pytest -q` = **428 passed / 3 skipped**（+4：test_pagination_keyset.py——过滤态游标续翻/未知游标宽容/before 紧邻回翻/after 链）
- web vitest **23 passed**（activity 多档 patch 测试按简化重写：-3 档间用例 +1 单档守卫）
- 双侧 typecheck/build、ruff、`pnpm gen` 确定性全绿（契约零变更：Page 形状/游标参数不动）
