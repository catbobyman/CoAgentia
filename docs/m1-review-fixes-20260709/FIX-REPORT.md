# M1 实机 Review 问题修复报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-09（America/Los_Angeles） |
| 分支 | `m1-impl` |
| 修复基线 | `609868f` |
| 范围 | M1 实机验证暴露的 7 项问题，以及验证过程中直接关联的错误响应/WS 清理问题 |
| 结论 | 7 项均已落地修复并补回归测试；真实 Server 同源和 Vite 开发代理均已浏览器验证 |

## 1. 修复总览

| # | 原问题 | 修复方案 | 主要文件 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | Web 固定访问 mock 端口，真实 Server 无静态托管/CORS，且仍调用 `/api/tasks`、`/__mock/play` | API 默认同源；Vite `/api` 代理到 8787；Server 自动托管 `apps/web/dist` 并支持 SPA fallback；mock-only 能力由 `VITE_MOCK_MODE` 显式开启 | `apps/web/src/api.ts`、`apps/web/vite.config.ts`、`apps/server/src/coagentia_server/app.py` | 已修复 |
| 2 | 仅凭 `X-Acting-Member` 即可冒充 Agent | 要求 Bearer Computer api-key，并联查 `members → agents → computers` 验证 Agent 隶属关系；无效、已删除、跨 Computer 全部 403 | `apps/server/src/coagentia_server/deps.py` | 已修复 |
| 3 | `@Hank，让他…` 把中文标点和后文吞进句柄 | 以当前有效成员名目录构造最长匹配，使用 Unicode 标点边界，不再把 `@` 后到空格的全文当句柄 | `apps/server/src/coagentia_server/routes/messages.py` | 已修复 |
| 4 | 未配对 Unicode surrogate 导致 SQLite 写入 500 | `MessageCreate.body` 在共享契约层验证 UTF-8 可编码性；校验错误序列化器同时处理 tuple、异常对象和 surrogate，稳定返回 422 | `packages/contracts/src/coagentia_contracts/rest.py`、`apps/server/src/coagentia_server/api.py` | 已修复 |
| 5 | 隔离 OAuth 凭证复制后会发生刷新竞争，坏凭证 Restart 不自愈 | 每次启动/投递前从机器级和同 daemon Agent 中选择过期时间最新的有效凭证并原子替换；认证失败时等待其他 Agent 刷新并自动重投失败 turn 一次 | `apps/daemon/src/coagentia_daemon/adapters/cmdline.py`、`claude_code.py` | 已修复 |
| 6 | 文件先移出 staging，数据库回滚后形成最终目录孤儿 | sidecar 保留到 DB commit；`Tx` 登记文件搬运，异常时逆序搬回 staging，提交后再删除 sidecar | `apps/server/src/coagentia_server/deps.py`、`files/store.py`、`routes/messages.py` | 已修复 |
| 7 | 390px 窄屏中 48px + 240px 固定栏挤压主区 | 720px 以下频道栏改为可开关抽屉，主区使用 `minmax(0,1fr)`；同步收紧消息、编辑器、详情、机器页和线程面板 | `apps/web/src/styles.css`、`Rail.tsx`、`ChannelList.tsx`、`RootLayout.tsx` | 已修复 |

## 2. 关键实现说明

### 2.1 真 Server 与 Web

- 生产/同源模式：先执行 `pnpm --filter @coagentia/web build`，`coagentia-server` 会自动发现 `apps/web/dist`；安装态可用 `COAGENTIA_WEB_DIST` 指定目录。
- 开发模式：Web 使用相对 `/api` 和 `/api/ws`，Vite 将 HTTP 与 WS 代理到 `http://127.0.0.1:8787`，不再依赖 CORS。
- mock 模式：只有 `VITE_MOCK_MODE=true` 才查询 M2 `/api/tasks` 并显示“播放时间线”。真实 M1 Server 不再产生这两类 404。
- `SpaStaticFiles` 对 `/computers`、`/agents/...` 等客户端路由回退 `index.html`，但不会吞掉 `/api/*` 404。
- 修正 Windows `.js` MIME，并给 favicon 提供无请求占位，浏览器控制台保持干净。

### 2.2 Agent 身份

Agent REST 请求现在必须同时具备：

1. `Authorization: Bearer <computer api-key>`；
2. `X-Acting-Member: <agent_member_id>`；
3. 该成员是未删除 Agent；
4. Agent 的 `computer_id` 对应 Bearer key 命中的 Computer。

浏览器不带 `X-Acting-Member` 时仍按 MVP 约定作为 Owner。携带伪造头时不再静默回退 Owner，而是明确返回 `403 PERMISSION_DENIED`。

### 2.3 Unicode 与 mention

- `MessageCreate.body` 在进入路由前执行 `value.encode("utf-8")`，未配对 surrogate 转为契约化 422。
- FastAPI 校验详情中的 `loc` tuple 转 list，`ctx` 异常剔除，非法字符串以反斜线转义，避免错误处理器自身再次 500。
- mention 解析只匹配实际成员名，并要求成员名后为结尾或非 `\w.-` 字符；`@Hank，`、`@Hank：`、`@Hank ` 均能命中。

### 2.4 OAuth 自愈

- 凭证候选包括机器级 `.claude/.credentials.json` 和同一 daemon `agents/*/.claude/` 下的同名文件。
- 排序优先级为：token 是否齐全、access token 过期时间、refresh token 过期时间、文件时间。
- 目标凭证只有在候选严格更新时才原子替换，避免有效凭证被旧机器副本降级。
- Claude 返回明确认证错误时，适配器短暂等待其他 Agent 完成刷新；发现更新凭证后将原 turn 自动写回 stdin 一次。
- 若机器级和所有 Agent 凭证都无效，仍需要用户完成一次 Claude 登录；这是外部认证前置条件，不再被伪装成可自动恢复的内部状态。

### 2.5 文件补偿事务

数据库与文件系统无法形成真正的单一 ACID 事务，因此实现为可恢复补偿：

1. `bind` 只移动正文，保留 staging sidecar；
2. 任一后续校验、INSERT 或 commit 失败，`get_tx` 回滚数据库并把已移动文件逆序搬回 staging；
3. commit 成功后删除 sidecar；
4. commit 后 sidecar 清理偶发失败只会留下无害 sidecar，正文与不可变 `files` 行仍一致。

## 3. 回归测试

新增或扩展的关键用例：

- 中文标点 mention 落库；
- 无 Bearer 冒充 Agent 返回 403；
- 有效 Computer key 的 Agent 作者归属正确；
- 已删除 Agent 即使持有旧 key 也返回 403；
- 未配对 surrogate 返回 422；
- 多附件中后一个无效时，第一个恢复 staging 且消息不落库；
- Server 静态首页、SPA 深链、API JSON、JavaScript MIME 同时正确；
- 凭证选择最新有效 peer；
- 认证失败后吸收 peer 凭证并自动重投 turn；
- 相对 API base 的 WS URL 生成与 HTTPS→WSS；
- WS 组件卸载会取消待执行重连 timer。

最终验证结果：

| 命令 | 结果 |
| --- | --- |
| `uv run pytest -q` | `238 passed, 2 skipped` |
| `pnpm typecheck` | 通过 |
| `pnpm --filter @coagentia/web test` | `10 passed` |
| `pnpm --filter @coagentia/web build` | 通过 |
| `uv run ruff check .` | 通过，原 6 个 E501 同批清理 |
| `uv run pyright` | 仍为 109 个既有 SQLAlchemy Core/RowMapping 类型问题，见 HANDOFF |

## 4. 实机验证

### 4.1 浏览器

- 真实 Server 直接同源托管构建产物：桌面 1440×900 正常，控制台 `0 errors / 0 warnings`。
- 默认开发组合：真实 Server 8787 + Vite 5173，REST/WS 代理正常，无 CORS、`/api/tasks`、mock timeline 或 favicon 错误。
- 390×844：主消息区、编辑器完整可用；频道按钮打开抽屉，遮罩与关闭行为正常。

### 4.2 REST/数据库

在真实迁移库上复验：

| 场景 | 结果 |
| --- | --- |
| 仅 `X-Acting-Member` 冒充 Pat | 403 |
| 正确 Computer Bearer + Pat | 201，`author_member_id` 为 Pat |
| `bad\\ud800` | 422 |
| `@Hank，请继续` | 201，`message_mentions` 精确落 Hank |

### 4.3 证据

- [桌面真实 Server](evidence/desktop-real-server.png)
- [390×844 会话](evidence/mobile-chat.png)
- [390×844 频道抽屉](evidence/mobile-channel-drawer.png)
- [Playwright trace](evidence/playwright.trace)

## 5. 未纳入本批的存量项

`pyright` 的 109 个错误在修复前后数量相同，主要来自 SQLAlchemy Declarative `__table__` 被推断为 `FromClause`、`RowMapping` 键类型和少量 stdio 类型。它们没有阻断本批运行验证，但当前仍不是静态类型全绿状态，已登记为下一批工程卫生任务。
