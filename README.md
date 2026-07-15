<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="site/assets/wordmark-dark.svg">
  <img src="site/assets/wordmark-light.svg" alt="CoAgentia" width="460">
</picture>

<h3>把 AI Agent 当同事用的本机多 Agent 协作平台</h3>

<p>从一句需求到上线 URL，全程发生在同一个 IM 里。</p>

<p>
  <img alt="pytest" src="https://img.shields.io/badge/pytest-1122_passed-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="vitest" src="https://img.shields.io/badge/vitest-512_passed-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="pyright" src="https://img.shields.io/badge/pyright-0_errors-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="milestones" src="https://img.shields.io/badge/MVP-M1–M8_shipped-E8763A?style=flat-square&labelColor=1A1D18">
</p>
<p>
  <img alt="python" src="https://img.shields.io/badge/Python-3.12+-E8763A?style=flat-square&labelColor=1A1D18&logo=python&logoColor=E9E7DC">
  <img alt="fastapi" src="https://img.shields.io/badge/FastAPI-server-E8763A?style=flat-square&labelColor=1A1D18&logo=fastapi&logoColor=E9E7DC">
  <img alt="react" src="https://img.shields.io/badge/React-web-E8763A?style=flat-square&labelColor=1A1D18&logo=react&logoColor=E9E7DC">
  <img alt="sqlite" src="https://img.shields.io/badge/SQLite-≥3.35-E8763A?style=flat-square&labelColor=1A1D18&logo=sqlite&logoColor=E9E7DC">
  <img alt="platform" src="https://img.shields.io/badge/Windows-单机_MVP-A5A69A?style=flat-square&labelColor=1A1D18">
</p>

<p>
  <a href="#核心能力"><b>核心能力</b></a> · <a href="#架构"><b>架构</b></a> · <a href="#仓库布局"><b>仓库布局</b></a> · <a href="#快速开始"><b>快速开始</b></a> · <a href="site/index.html"><b>项目介绍页</b></a>
</p>

<p>
  <a href="README_EN.md">English</a> | <b>中文</b> | <a href="README_JA.md">日本語</a>
</p>

<code>需求消息 → 拆解提案 → 确认落地 → 并行交付 → Diff/预览验收 → 合并 → 一键部署 → 成本核算</code>

</div>

---

**CoAgentia** 是一个**契约驱动、流程可编排、护栏可干预**的多 Agent 协作工作台。人类和 AI Agent 在频道里像同事一样对话；Orchestrator 把需求拆解成任务图，多个 Agent 并行写码交付；你在 Diff 与实时预览里验收，点一下合并、再点一下部署，最后拿到 URL 和这单活的 token 账单。

## 为什么是 IM

Agent 编排工具大多长成「工作流画布 + 日志控制台」。CoAgentia 的判断是：**协作的自然形态是对话**。频道、线程、@提及、任务看板——人类团队怎么协作，人机混合团队就怎么协作。画布、护栏、账本都长在对话旁边，而不是把对话塞进控制台。

## 核心能力

| 域 | 能做什么 |
| --- | --- |
| **IM 基座** | 频道 / DM / 线程 / @提及 / 文件 / 已读；WS 事件驱动，全程无刷新 |
| **任务域** | 消息一键转任务、认领 / 指派 / 状态机、看板、全文搜索、Activity 流 |
| **编排画布** | React Flow 任务依赖图；防成环写事务；`blocked` 实时推导 + 投递层 gating；force-start 人工干预 |
| **Orchestrator 拆解链** | 顶级 @Orchestrator 即拆解：提案 → 结构校验（14 条规则）→ 草稿层人工确认（可逐项调整）→ 原子落地；后续可发增量 delta 修订 |
| **交付链** | `writes_code` 任务自动派生 git worktree；Diff 查看、**长驻 dev server 实时预览**（iframe 并排验收）、DAG 序 `merge --no-ff`、冲突自动建任务派回 |
| **一键部署** | 人类点击或 Agent 调 `trigger_deploy` 双通道；部署日志实时流、串行 409 保护、结果卡带 URL |
| **护栏** | 沉默提醒升级链、freshness 门 + 草稿拦截三键、O8 汇总轮护栏（防空转自激）、质量信号回路 |
| **成本核算** | `GET /usage` 工作区 / Agent / 任务三层归属；部署新账 token 小结；覆盖率诚实标注，永不假装折算货币 |
| **双 runtime** | Claude Code 与 Codex CLI 适配器；每频道通知策略、cron、技能白名单、角色模板与三步建队向导 |

## 架构

```
┌──────────────┐    REST + WebSocket    ┌───────────────────┐
│   apps/web   │ <====================> │    apps/server    │
│ React + Vite │                        │ FastAPI + SQLite  │
└──────────────┘                        └─────────┬─────────┘
                                                  │  WS frames (Contract D)
                                        ┌─────────┴─────────┐
                                        │    apps/daemon    │
                                        │   executor only   │
                                        └─────────┬─────────┘
                                                  │  stdio / JSON-RPC
                                        ┌─────────┴─────────┐
                                        │ Claude Code/Codex │
                                        │   CLI, MCP x16    │
                                        └───────────────────┘
```

- **server 是唯一裁决者**：gating、DAG 序、冲突处置、触发判定全部在 server；daemon 只执行。
- **契约先行**：实体 / REST / WS 事件 / daemon 帧 / 常量目录先在契约文档定版，代码是契约的填空。`packages/contracts`（Pydantic v2）是仓库内唯一类型源，TS 类型经 `pnpm gen` 生成，禁止手改。
- **同构内核单源**：图推导 / 指纹 / 拆解校验三组确定性内核 = Python 权威实现 + TypeScript 镜像 + 金标判例双跑逐字节对照，语义漂移直接红。

## 仓库布局

| 路径 | 内容 |
| --- | --- |
| `packages/contracts` | 【唯一源】Pydantic v2 模型 + `kernel/` 确定性内核 |
| `packages/contracts-ts` | 【生成物】TS 类型，`pnpm gen` 重新生成 |
| `packages/fixtures` | 样例数据 + `golden/` 跨语言金标判例 |
| `apps/server` | FastAPI 主服务（REST / WS / 编排 / 护栏 / 部署） |
| `apps/daemon` | Agent 执行器（CLI 适配器 / 预览 / 部署 runner） |
| `apps/web` | React 前端（Afterglow 设计语言） |
| `apps/mock-server` | 契约驱动 mock（fixtures over REST + WS） |
| `site/` | 项目介绍页（静态单文件） |

## 快速开始

**环境要求**：Windows（当前为单机 MVP 目标平台）· Python ≥ 3.12 + [uv](https://docs.astral.sh/uv/) · Node.js + pnpm 10 · SQLite ≥ 3.35 · 已登录的 [Claude Code](https://claude.com/claude-code) CLI（真 Agent 运行所需）

```bash
# 安装
uv sync
pnpm install

# 终端 1：后端
uv run coagentia-server            # http://127.0.0.1:8787

# 终端 2：前端
pnpm --filter @coagentia/web dev   # http://127.0.0.1:5173（代理 /api → 8787）
```

同源单进程运行：`pnpm --filter @coagentia/web build` 后直接 `uv run coagentia-server`，打开 `http://127.0.0.1:8787`。

## 开发守门（全绿才算完成）

```bash
uv run pytest -q                    # 后端 + 契约测试（当前 1122 passed）
pnpm -F @coagentia/web test         # 前端 vitest（当前 512）
pnpm typecheck                      # pyright 0 错 + 双 tsc
uv run ruff check .
pnpm gen                            # 生成后 git diff 必须为空（生成物确定性）
pnpm -F @coagentia/web build
```

## 现状

MVP 全部规划里程碑（M1–M8）已收口：IM 基座 → 任务域 → 契约与画布 → 护栏 → 双 runtime 与模板 → 交付链 → 拆解链 → 预览 / 部署 / 编排质量线。每个里程碑均以隔离环境实机 verify（真 server + 真 daemon + 真 git 子进程）加对抗式代码复审收口。

已知边界：单机、单工作区、单用户信任模型；多用户 / 多租户 / 多机是后续路线。
