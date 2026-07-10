# 挂账批1 实机证据：消息流附件卡数据源修复（channelFiles ≤50 截断）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-10 |
| 出处 | M2 挂账（M3-HANDOFF §8 首行）：附件卡数据源 = channelFiles 首页 ≤50，旧文件缺附件卡 |
| 修法 | **消息级关联**（契约 A **v1.0.4**）：`MessagePublic` 增读面派生字段 `files`（Public≠Row 放宽第 5 例，同 v1.0.3 `actor_member_id` 先例）；REST 消息读面（列表/线程/发消息响应/搜索命中）与 `message.created` 广播附着（`[]`=无附件），daemon 帧未附着面保持 `null`；前端 `MessageFlow` 直接消费 `m.files`，删 `filesByMessage` 三处透传；`files` 表补索引 `(message_id)`/`(channel_id, id)`（迁移 0004，if_not_exists 兼容 0001 create_all 已建路径——坑1 索引变体） |
| 方式 | 真 uvicorn（独立 launcher，临时库 alembic head + seed，端口 8803）+ 真 HTTP（httpx）+ 浏览器同源（playwright 1440×900） |

## A. 真 HTTP probe：9/9 PASS（scratchpad b1_probe.py）

55 个文件分别绑到 55 条消息（穿透 channelFiles 首页默认 50）：

1. PASS — post_message 响应自带 files
2. PASS — 响应 files 剔除 stored_path（FilePublic 形状）
3. PASS — channelFiles 首页 = 50 且有 next_cursor（**截断前提在场**）
4. PASS — 最老文件不在 channelFiles 首页（旧路径该消息必缺卡）
5. PASS — 55/55 消息读面 files 全对齐（含最老第 1 条，超出首页 50）
6. PASS — 无附件消息 files == []（已附着态，非 null）
7. PASS — 线程根消息 files 附着（thread 端点）
8. PASS — 线程回复 files 附着
9. PASS — 搜索命中 files 附着

## B. 浏览器实证（b1-attach-oldest-message.png）

#all 频道滚动至最老 probe 消息：`文件消息 00–06` 逐条渲染 `att-00.md…` 附件卡；
页内 55 条 probe 消息共 56 张附件卡（55 主流 + 1 线程回复）；同屏「文件」页签徽标 = **50**
（channelFiles 首页上限仍在），直接目证附件卡已与其解耦。

## C. 守门基线

- 后端 `pytest -q` = **424 passed / 3 skipped**（+3：读面派生逐面测试、0004 索引断言、public-shapes 第 5 放宽例）
- web vitest **25 passed**（+2：MessageFlow 附件卡消费 m.files / []·null 不渲染不崩）
- 双侧 typecheck/build、ruff、`pnpm gen` 确定性全绿
- 契约 A 文档升 **v1.0.4**（engineering_docs/01：header + messages/files 表段 + 变更记录）
