# M5-DEV-PLAN —— 逐模块执行计划与进度表

> 任务书 = [M5-HANDOFF.md](M5-HANDOFF.md)（范围/裁决/出口清单权威）。本文只跟踪执行进度与波次编排。体例同 [M4-DEV-PLAN.md](M4-DEV-PLAN.md)。

## 0. 编排策略（多 agent 并行，两波 + 波间 inline 守门）

**H2 外部不确定性已在立项会话消除**：`codex app-server generate-json-schema` 生成完整协议 + 最小 initialize/skills/list/model/list 冒烟成功 → 校准结论 = `scratchpad/CODEX-CALIBRATION.md`（H2 权威实现参考，推翻裁决 #11「codex 无技能」——codex 有 `skills/list`）。H2 由「探测未知」降为「照已知协议填空」。

| 波 | 模块（并行） | 文件域（不相交） | 依赖 |
| --- | --- | --- | --- |
| **波 1 地基** | H0 契约 ∥ H1 迁移+ORM ∥ H2 Codex 适配器 | packages/contracts(+ts,+mock) / apps/server(db,migrations) / apps/daemon | 无（H0/H1/H2 互不依赖；H2 吃 CALIBRATION 文件） |
| — inline 守门 1 | 主循环跑 pytest+gen+daemon import；修集成缝 | — | 波 1 全绿才进波 2 |
| **波 2 特性** | H3 通知端点+mode 门 ∥ H4 cron 服务侧 ∥ B-M5-1 前端 | apps/server(channels/messages/activity) / apps/server(members/reminders) / apps/web | H3/H4 吃 H0+H1；B-M5-1 吃 H0 mock 形状 |
| — inline 守门 2 | 全量测试+typecheck+ruff+gen+双 build | — | 全绿 |
| **实机 verify** | 隔离库+真 codex CLI：创建 codex Agent→混 runtime 对话；通知 mute；cron 触发 | — | 出口锚点 |
| **/code-review high** | 8 角度 review→修 | — | 收口 |

- 契约集中：**H0 独占 packages/contracts 全部改动**（含 cron 解析器 + test_conformance_dual.py 全部 M5 端点），波 2 agent 不碰 contracts 与 conformance 测试，避免包内竞态。
- H1 独占 models.py（templates + channel_notification_settings 双 ORM + M5_TABLES），H3 只消费不改 models.py。
- route 注册：H3 的 notification-setting 挂 channels.py（已注册）、H4 扩 members.py（已注册）——**波 2 无人改 routes/__init__.py**（templates 新模块归 M5b）。

## 1. 进度表

| # | 模块 | 状态 | 提交 | 备注 |
| --- | --- | --- | --- | --- |
| H0 | 契约登记（ENDPOINTS_M5/TemplateBody 下沉/请求响应模型/NotificationSettingPut/ChannelsSnapshot 扩/DetectedRuntime.skills/错误码 25/mock/conformance） | ✅ | `7d06e8c` | 波 1 |
| H1 | 0007 迁移 + models.py 双 ORM + M5_TABLES | ✅ | `7d06e8c` | 波 1 |
| H2 | Codex 适配器（CodexProcess/_launch 分派/probe_codex+skills/CODEX_HOME 隔离/三档重置 resume）+ E2 v1.0.1 实测校准 | ✅ | `7d06e8c` | 波 1 |
| H3 | 通知端点 GET/PUT + mode 消费门（mute 掐 mention activity 生成）+ ChannelsSnapshot 扩字段 | ✅ | `38c4ea5` | 波 2 |
| H4 | cron cadence 服务侧（手写 5 段 cron.py+cadence.py 单点 + 塌缩重排 + 三处同门） | ✅ | `38c4ea5` | 波 2 |
| B-M5-1 | 技能编辑 + 频道设置弹窗（含 P12 阈值收编）+ cron 显示 + 通知徽标 | ✅ | `38c4ea5` | 波 2 |
| — | 实机 verify → [M5A-EVIDENCE.md](../verify/M5A-EVIDENCE.md)（codex 真机 PONG+usage / REST 9/9 / 2 截图 / console 0） | ✅ | `da6833a` | 重跑实测 |
| — | /code-review high（8 维度→5 CONFIRMED 全修：cron 500/DST、通知 TOCTOU、codex 凭证、probe symlink） | ✅ | `da6833a` | 每项 HTTP/单测实证 |

## 2. 守门命令（波间与收口）

```
uv run pytest -q                    # 572 passed 基线，只增不减
pnpm -F @coagentia/web test         # vitest 106 基线
pnpm typecheck                      # pyright 0 + 双 tsc
uv run ruff check .
pnpm gen                            # diff 空
pnpm -F @coagentia/web build
```

## 3. 关键实现锚点（防返工）

- **codex 协议真值**：`scratchpad/CODEX-CALIBRATION.md`（传输/握手/thread/turn/事件映射/usage/skills 全表）+ `scratchpad/codex_schema/`（516 defs v2 schema）+ `scratchpad/codex_smoke2.py`（可跑骨架）。
- **codex 命令行**：裸 `codex app-server`（win32 = codex.cmd + **taskkill /F /T 杀进程树**，terminate 无效）；stdout **必须 utf-8 decode**（gbk 会崩）。
- **CODEX_HOME 隔离**：per-Agent 目录，config.toml 注入 `[mcp_servers.coagentia]`；approvalPolicy=never + sandbox=danger-full-access（NFR5）。
- **管理器分派点**：`claude_code.py:392-396` `_launch` 写死 ClaudeCodeProcess → 按 `boot.runtime` 分派；`cli.py:51` 解除单 adapter 写死（管理器骨架 runtime 无关原样复用）。
- **mode 门单点**：activity emit 前查接收者 `channel_notification_settings.mode`；mute→不生成 mention activity（dm activity 恒生成，DM 必达）。
- **cron 塌缩**：照 `reminders/interval.py::next_after` 的 interval 塌缩语义加 cron 分支（now 之后首个命中，停机不逐格重放）。
