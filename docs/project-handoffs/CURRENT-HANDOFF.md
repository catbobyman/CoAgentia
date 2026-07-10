# M1 Review 修复交接

## 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia` |
| 分支 | `main` |
| 合并提交 | `f2c993f merge: complete M1 implementation and hardening` |
| 修复提交 | `351684a fix: harden M1 runtime and consolidate handoffs` |
| 工作树 | 干净；M1 实现与 hardening 已合并到 `main` |
| M1 核心闭环 | 既有真实两 Agent 对话/文件产出成立；本批修复实机 review 暴露的 7 个问题 |
| 自动测试 | 238 passed，2 skipped；Web 10 passed；双侧 typecheck、build、ruff 全绿 |
| 浏览器实证 | 同源 8787 与 Vite 5173 均能连接真实 Server；桌面/390px 已目检 |

详细变更、验证矩阵和证据见 [FIX-REPORT.md](../m1-review-fixes-20260709/FIX-REPORT.md)。

## 已完成

1. 真 Server 可托管 Web dist，Vite 默认代理真实 Server，mock-only 行为显式隔离。
2. Agent REST 身份改为 Computer Bearer + Agent 隶属关系双校验。
3. 中文标点 mention、未配对 Unicode 500、错误响应二次 500 已修复。
4. OAuth 隔离凭证支持 peer 选优、原子同步和一次失败 turn 自动重投。
5. 文件绑定增加数据库回滚补偿，不再丢 staging 或制造最终目录孤儿。
6. 窄屏频道抽屉和主区响应式布局完成，WS 重连 timer 清理完成。
7. 新增回归测试并将 Ruff 从 6 个错误清到全绿。

## 启动方式

### 真实开发模式

终端 1：

```powershell
uv run coagentia-server
```

终端 2：

```powershell
pnpm --filter @coagentia/web dev
```

打开 `http://127.0.0.1:5173`。Vite 将 `/api` 和 `/api/ws` 代理到 8787。

### 同源构建模式

```powershell
pnpm --filter @coagentia/web build
uv run coagentia-server
```

打开 `http://127.0.0.1:8787`。Server 在 monorepo 中自动发现 `apps/web/dist`；安装/部署到其他目录时设置 `COAGENTIA_WEB_DIST`。

### Mock 模式

Mock 不再是默认路径。需要时显式设置：

```powershell
$env:VITE_API_BASE='http://127.0.0.1:8642'
$env:VITE_MOCK_MODE='true'
pnpm --filter @coagentia/web dev
```

## 下一步任务

### P0：实机补充验证

1. 在已登录 Claude 的干净机器状态下再跑一次双 Agent 并发冷启动，重点观察真实 OAuth refresh 竞争后的自动重投；当前已有确定性单测和凭证 peer 自愈实测，但未在本批重新消耗完整双 Agent 对话。

### P1：静态类型债务

`uv run pyright` 仍有 109 个既有错误。建议单独开批处理，不与业务修复混在一起：

1. 为 SQLAlchemy `Model.__table__` 统一提供 `Table` 类型 helper/cast，消除大部分 `FromClause` DML 报错。
2. 对 `.mappings().first()` 建立非空窄化 helper，统一 `RowMapping → dict[str, Any]`。
3. 修正 `frames.py` 的 tool id 可空键、`mcp.py` stdio 文本/二进制类型和 `api.py` JsonValue 标注。
4. 把 `pyright` 加入 CI 后再宣称 Python 静态检查全绿。

### P2：产品推进

1. **已立项 M2**（owner 决策 2026-07-09）：任务书 = [M2-HANDOFF.md](M2-HANDOFF.md)；配套契约修订已完成（A v1.0.2 / B v1.1 / E v1.1，见 engineering_docs）。**两块执行**（owner 确认切分）：块 M2a 任务闭环先收（收口即 PRD M2 出口），块 M2b 发现与聚合面随后。开工第一步 = C0 契约登记 + C1 建表。
2. 前端附件展示已并入 M2 任务书 B-M2-3（文件页签同批补消息内附件卡）；批 6 四屏为伴随任务不进 M2 出口门。
3. 更新根级 `M1-DEV-PLAN.md` 的测试数字；阶段性结论统一沉淀到本目录的 `PROJECT-RECORD.md`，避免多份状态文档继续漂移。

## 注意事项

- 若机器级和所有 Agent OAuth 凭证都已失效，自动选优没有可用来源，必须先执行一次 Claude 登录。
- mock 模式必须显式开启，否则任务查询返回本地空数组，不会向 M1 Server 请求不存在的 `/api/tasks`。
- 文件绑定现在具备请求级补偿；极端的“DB 已提交但进程在 sidecar 删除前崩溃”只会留下 sidecar，不会丢正文或 files 行。
- 实机验证生成的服务、Vite、浏览器和 Claude 进程均应在会话结束前关闭；本批验证端口为 5173/8787/8788。
