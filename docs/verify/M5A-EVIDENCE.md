# M5a 实机 verify 证据（第二 runtime 与配置面）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-11 |
| 范围 | 块 **M5a**（H0 契约 / H1 迁移 / H2 Codex 适配器 / H3 通知端点+mode 门 / H4 cron / B-M5-1 前端 + code-review 5 修复） |
| 环境 | 真 codex-cli **0.144.0**（ChatGPT 登录态）· 隔离临时库 alembic upgrade head（含 0007）+ seed · 真 uvicorn（create_app）· playwright 独立 chromium 1440×900 |
| 结论 | **codex 真机对话 PASS + REST 端到端 9/9 PASS（含 2 项 code-review 修复）+ 2 截图（已核验内容）+ console 0** |

> 本证据为**重跑实测**（非仅测试通过）：codex turn 与 REST 探针均现场对真 codex CLI / 真 uvicorn 运行，输出如下。

## 1. Codex 适配器真机完整 turn 对话（第二 runtime 跑通的核心独立证据）

探针 `scratchpad/m5a_codex_turn2.py`：H2 `codex_cmdline.materialize_credentials` 物化 `~/.codex/auth.json` 进隔离 `CODEX_HOME` → 起真 `codex app-server` → initialize → thread/start（sandbox=danger-full-access / approvalPolicy=never）→ turn/start → 收集回复。**2026-07-11 重跑输出**：

```json
{ "credentials_copied": ["auth.json"],
  "codex_home": "...\\Temp\\m5a_t2_aj7kk8vs\\.codex",
  "thread_started": true, "turn_completed": true, "turn_status": "completed",
  "agent_reply": "PONG", "reply_has_PONG": true,
  "usage": {"totalTokens":12126,"inputTokens":12120,"cachedInputTokens":9984,"outputTokens":6},
  "error_count": 0 }
=== M5a codex turn verify: PASS ===
```

- **codex 真回复 "PONG"** + usage 提取（input 12120 / output 6 / cache_read 9984，CALIBRATION §7 字段映射）+ 0 error。
- **反证坐实凭证物化必要性**：首次实测用空隔离 CODEX_HOME（未物化）→ codex 连 `wss://api.openai.com/v1/responses` 返回 **401 Unauthorized**（10 error 帧 / turn failed）。物化 auth.json 后 → PONG。证明 `materialize_credentials` 是 codex Agent 可用的必要步骤。

## 2. runtime 探测真机（probe_runtimes）

真机 `probe_runtimes()`：claude_code installed / 3 models / **18 skills**（`~/.claude/skills/` 扫描）；codex installed / **7 models**（gpt-5.6-sol/terra/luna…）/ **22 skills**（app-server `skills/list`）。**裁决 #11 推翻**：codex 有技能机制，候选池非空。

## 3. REST 端到端（真 uvicorn 8802；探针 `scratchpad/m5a_rest_probe.py`）——**2026-07-11 重跑 9/9 PASS**

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 创建 codex runtime Agent（POST /agents runtime=codex）→ 201 + 落库 runtime=codex | PASS |
| 2 | 技能白名单 PUT+GET 往返一致 | PASS |
| 3 | 通知 mute 端到端：mute 前 @Owner→mention 生成（before=1）；PUT mute；mute 后再 @Owner→mention 不新增 | PASS |
| 4 | DM 频道通知设置 → 422 NOTIF_IN_DM | PASS |
| 5 | cron reminder：201 + next_fire_at 逐字节 == cadence 单点重算 + 本地周五 09:00（PDT=UTC16:00） | PASS |
| 6 | 非法 cron（99 分）→ 422 | PASS |
| **6b** | **[review 修复] impossible cron（2/30）→ 422 而非 500** | **PASS** |
| **6c** | **[review 修复] 通知 PUT 幂等（连续双 PUT 均 200，原子 upsert 消 TOCTOU）** | **PASS** |
| 7 | ChannelsSnapshot.notification_settings 含本人 mute 行 | PASS |

> 过程坐实 **M4 freshness 护栏对 codex/agent 主体仍生效**：Pat（agent 主体）在有未读的 #all 发言首次被 M4 门扣成 202；探针给 Pat 手设 read_position（真机由 daemon deliver 推进）后正常发言。

## 4. 前端可视（playwright 1440×900，console 0 错误）

| 截图 | 内容（已逐张核验渲染） |
| --- | --- |
| [m5a-skills-codex-pool.png](m5a-skills-codex-pool.png) | Agent 详情技能页（codex Hank）：**22 技能候选池**全显示（academic-paper-composer/anysearch✓/browser:*/playwright/visualize/imagegen/openai-docs…）+ 已勾选 + 池外标记 + 自由输入框 |
| [m5a-channel-settings.png](m5a-channel-settings.png) | 频道设置弹窗四组：基本 / 通知（全部·仅@·静音 + 「DM 必达不受此设置影响」）/ 提醒阈值（Todo24·InProg12·InReview24 + 升级链）/ 护栏阈值（自动重评估5分·连续被扣升级3次）——**P12 阈值挂账 UI 收编** |

> B-M5-1 前端逻辑另由 vitest（SkillsTab/ChannelSettingsModal/cron/notify）覆盖。

## 5. code-review high（8 维度 finder→对抗性 verify）5 CONFIRMED 全修 + 实测

| # | 缺陷 | 修复 | 实测证据 |
| --- | --- | --- | --- |
| 1 | impossible cron → HTTP 500（initial_fire 在 try 外裸抛 ValueError） | `validate()` 加可满足性探测单点 → 422 | §3 #6b（真 HTTP 422） + `test_recurring_reminder_impossible_cron_422` |
| 2 | DST 回拨 rearm next_fire_at≤now → 每 5s 重复触发 | `next_after` 加 UTC 严格比较循环兜底 fold | `test_next_after_strictly_later_utc_invariant` |
| 3 | 通知 PUT TOCTOU（并发双 PUT 复合 PK 冲突 500） | SQLite 原子 `on_conflict_do_update` | §3 #6c（双 PUT 均 200） |
| 4 | codex `materialize_credentials` 无条件覆写回退刷新的 OAuth | mtime 新鲜度选优（保留刷新态） | `test_codex_materialize_credentials_preserves_refreshed` |
| 5 | `scan_claude_skills` 跟随 symlink 越界/循环 | 跳过 symlink 不跟随 | `test_scan_claude_skills_skips_symlinks` |

## 6. 守门基线（M5a 收口态）

```
uv run pytest -q          → 672 passed / 4 skipped（含 code-review 5 修复的回归测试）
pnpm typecheck            → pyright 0 + 双 tsc
uv run ruff check .       → 干净
pnpm gen                 → diff 空
```

## 7. 探针脚本（scratchpad，可复跑）

- `CODEX-CALIBRATION.md` + `codex_schema/`：codex app-server 协议实测校准。
- `m5a_codex_turn2.py`：codex 真机 turn（凭证物化 + PONG + usage）。
- `m5a_rest_probe.py`：REST 端到端 9 checks（含 2 项 review 修复的 HTTP 实证）。
- `m5a_shot_server.py`：前端截图 server（seed + 真机探测 skills 注入 + dist）。
