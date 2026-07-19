# DEDAG 实机铺开验证实证（R1/R2/R3）

日期：2026-07-19 ·执行：Fable（Claude Code 会话，owner 拍板接续项 ①「实机铺开委派模式」）
本文是 DEDAG 重构（main merge `09c2269`）后**委派模式在真 CLI 下的首次多任务实战验证**记录。
原始数据：`DEDAG-ROLLOUT-CHANNEL-LOG.txt`（realtest 频道全量 98 条消息 + 任务/worktree 终态）。

## 0. 环境

- 真 uvicorn（8787，`~/.coagentia/server/coagentia.db`，alembic 0013）+ 真 daemon（RealTest-PC）。
- 真 CLI：Orch-Main / Dev-Claude-A = claude CLI 2.1.211；Dev-Codex-A = codex-cli 0.144.0。
- 项目 pomodoro-demo（`D:/Project4work/Agenthub_7_8/scratch-pomodoro`，单文件 `pomodoro.html`）。
- 频道 realtest = `01KXHXW27P1P1D5G94H1V5HA04`。

## 1. R1 单任务冒烟（#11 键盘快捷键）——0 nudge，4 分 39 秒闭环

| 时刻 (UTC) | 事件 |
|---|---|
| 05:49:02 | Owner 发需求 @Orch-Main（快捷键 + 底部说明，只改 pomodoro.html） |
| 05:51:38 | Orch-Main `create_task` #11 派活 @Dev-Codex-A（验收标准逐条可证伪、附验证命令） |
| 05:51:54 | Dev-Codex-A 自主认领（claim） |
| 05:53:01 | 交付：commit `3d83a9b` + **task_handoff 自主提交**（`01KXWEYED0…VK63`）→ in_review |
| 05:53:41 | Orch-Main 验收 → `trigger_merge` → ✅ merged `4ccba0a2`（真 git --no-ff） |
| 05:54:02 | Orch-Main 群内汇报（含遗留交互 AC 提示）；**顺手关闭滞留 todo 的旧任务 #1/#2/#7** |

需求→合并主干全程 **4 分 39 秒，零人工干预**。task_handoff 契约提交自主完成（无 422 退回）。

## 2. R2 三任务并行 + 真冲突派回——1 nudge，冲突链全设计面命中

需求（05:56:26，Owner）：三件套升级 ①主题切换 ②音效提醒 ③长休息机制，全部只改
`pomodoro.html`（**故意同文件制造冲突面**），要求并行派活 + 串行合并。

| 时刻 (UTC) | 事件 |
|---|---|
| 05:57:52–05:58:06 | Orch-Main 连发三条 create_task：#12①/#13②→@Dev-Codex-A，#14③→@Dev-Claude-A，并发布派活与合并计划 |
| 05:59 前后 | 三任务并行认领开工（各自独立 worktree） |
| ~06:00 | #12 交付 in_review → Orch-Main 验收 → ✅ merged `ee654074` |
| 06:01:38 | #13 触发合并 → **❌ 真 git 冲突**（与 #12 同文件同区）→ 系统消息 + `merge_conflict` 卡 + **自动建冲突任务 #15 派回 Dev-Codex-A** |
| 06:01:43 | Dev-Codex-A 应答冲突处理方案；Orch-Main 发合并进度指挥（保留 ① 主题变量 + ② 音效逻辑） |
| 06:02:00 | #14 交付 in_review（Dev-Claude-A，正文**无任何 @**） |
| 06:03:01 | 冲突在原 worktree 解决（`c01fdd1`）→ 重触发 → ✅ #13 merged `ef46463f`（alias 行 #15 同步 merged） |
| 06:03–07:20 | **停滞 76 分钟**：#14 停在 in_review，无人验收（唤醒缺口，见 §4-1） |
| 07:20:xx | 人类 nudge ×1（Owner @Orch-Main：验收 #14、闭环 #15、发总报告） |
| ≤07:21 | **45 秒内**：#14 验收 → ✅ merged `47a6513a` → #15 closed → 总交付报告发出（①达成②证据③未覆盖④风险四段完整，含全部 commit 号） |

### 主干终态（git 实证）

```
47a6513 merge #14 ③ 长休息机制 + 轮次进度点
ef46463 merge #13 ② 阶段结束音效提醒     ← 含冲突解决 c01fdd1
ee65407 merge #12 ① 主题切换（浅色/深色）
4ccba0a merge #11 键盘快捷键 + 底部说明   ← R1
```

特性核验（grep 主干 `pomodoro.html`）：`pomodoroTheme`×2 · `pomodoroSoundEnabled`×2 +
`AudioContext`×7 · `LONG_BREAK(_TIME)`×8 + `updateProgressDots` 在——三特性全部在主干。

### 本批设计核心面全部真机命中

- 202 异步合并、同项目串行（W5）、真 git --no-ff；
- **冲突 → 自动建「解决冲突」任务派回原负责人**（merge_conflict 卡 + @提及唤醒）；
- 原 worktree 内解决 → 重触发续合（重试语义）；alias 行随原任务合并同步 merged；
- Orchestrator 冲突指挥话术生效（明确保留双方哪些改动，Codex 按指挥执行）。

## 3. B-6 判定（M8 遗留：交付收尾冷启动话术）

**原口径不复现**：R1 零 nudge 全链自治（M8 同链需 1 次 nudge）。R2 的 1 次 nudge 根因是
交付唤醒缺口（§4-1），属新形态，不是 M8 的冷启动收尾问题。B-6 可销账，由 §4-1 接棒。

## 4. 发现与处置

### 4-1. 交付唤醒缺口（R2 主发现，已修话术）

**现象**：#14 交付消息正文无任何 @ → 协调者非常驻、只有被 @（服务端从 body 解析落
mention 行）或被人类唤醒才行动 → in_review 停滞 76 分钟。#12/#13 未踩中是因为验收发生在
Orch-Main 上一个活跃会话期内（时间窗掩盖）。

**修复（本批提交，零契约变更——两处均为产品文案面）**：
1. daemon `_IDENTITY_TEMPLATE`【交付纪律】追加：置 in_review 后须在频道发交付消息并
   @ 派活人（claude/codex 双 CLI 共用 `build_identity_prompt` 单源）；
2. Orchestrator 模板第 2 条（派活）：任务 body 末尾固定写明「交付 @ 你」；第 3 条（盯交
   付）：点破自己非常驻、「交付 @ 你」是盯得住的前提；
3. 测试锚 +2（`test_delivery_wake_discipline_present` / daemon identity 断言），只增不减。

**候选挂账（机制侧，owner 拍板）**：任务置 in_review 时引擎自动唤醒/通知 task 创建者
（不依赖话术遵从）。属行为面新增，需过 hub 唤醒语义评审，本批不动手。

### 4-2. 目录系统消息重复 ×2（观察项，非 DEDAG 回归）

同一任务的「[系统工作目录]」系统消息连发两条（~10s 间隔，R1 #11 与 R2 #12 均出现）。
经查 2026-07-15 M8 实测日志中已存在同款重复——**既有潜伏面**，挂账不顺手修。

### 4-3. 话术质量正面样本（无需动作）

- Orch-Main 派活验收标准逐条可证伪 + 附验证命令（模板第 2 条兑现）；
- 总交付报告四段式完整、诚实标注交互类 AC 未实测（第 5 条兑现）；
- 冲突后 Dev-Codex-A 先复述处理方案再动手（第 7 条精神在 dev 侧自发出现）。

## 5. 结论

委派模式（create_task 派活 → 盯交付 → trigger_merge 指挥合并 → 汇总报告）在真 CLI 多任务
并行 + 真冲突场景下全链贯通：**R1 零 nudge、R2 仅 1 nudge 且根因已修**。冲突自动派回/重试
/alias 同步等 DEDAG 新增语义全部真机命中。B-6 销账。
