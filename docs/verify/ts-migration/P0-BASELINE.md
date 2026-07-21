# P0 可复现基线

> 状态：COMPLETE（reviewed/accepted）
> 阶段：P0「基线、范围和当前试验裁决」
> 执行规则来源：[仓库根 plan.md](../../../plan.md)
> 计划代码基准：`54f1372`
> P0 产品执行起点：`cee5d98577e81c43264db15ebcb24cdbdcfcf436`
> P0 工具/CI 修复基线：`f90d4234bc4303ed4980d19898adca50ed804f8a`
> P0 冻结制品基线：`a2fa540036be69a79774d1b5f446fd02bd5935f3`
> 制品基线 tree：`d257fef2617458f18e9dd98dc292c6c2ec6fc75a`
> 当前执行分支：`main`
> 证据日期：2026-07-20

## 1. 基线边界

- owner 已批准仓库根 `plan.md` v1.0 的「全仓纯 TypeScript / `.py=0` / TS 契约单源」终态。
- P0 不修改产品契约，不删除 Python，不切默认 server/daemon，不改生产数据库。
- `cee5d98577e81c43264db15ebcb24cdbdcfcf436` 是产品表面的执行起点；P0 工具、workflow 与 authority 首个实现提交为 `27c044dfdf97283f7618a6d454a1499fe8e75fec`，fresh-checkout fingerprint 修复提交为 `a7de995590e9d59cae6d4a9df02fdc99627e8c3a`。首次外部 Windows runner 实证出的 guard/history 两项机制缺陷由 `238aef645338ca4fe105c6a2d913a64a99343551` 修复；第二次 runner 又实证 fresh clone 缺少 ignored `build/*.json` 及 Windows 生成物行尾导致 clean gate 误脏，最终由 `f90d4234bc4303ed4980d19898adca50ed804f8a` 修复。当前冻结 JSON 制品统一绑定其 evidence-only 后继 `a2fa540036be69a79774d1b5f446fd02bd5935f3`。
- 开工前的 7 处 daemon 分发/退役试验已原样冻结于本地分支 `codex/p0-daemon-distribution-spike`（`96492b8`），不计入绿基线；裁决见 [P0-EXPERIMENT-DECISIONS.md](P0-EXPERIMENT-DECISIONS.md)。
- owner 已授权本轮 push；npm 发布与其他外部数据写入仍未授权。P0 GitHub Actions 的新鲜 Windows runner 结果必须逐次归档 URL/SHA。

## 2. 支持矩阵与规划机环境

| 层级 | P0 声明 | 规划机实况 |
|---|---|---|
| OS/CPU | Windows 11 x64，AMD64/x64 为当前必过矩阵 | Windows 11 Home `10.0.26200` / Ryzen 9 8945HS（8C/16T） |
| Hermetic core | Node 22 + pnpm；不得调用 Python/node-gyp | Node `22.22.1`，pnpm `10.6.2` |
| Legacy oracle | Python/uv 仅在清场前作冻结 oracle | Python `3.14.0`，uv `0.9.0` |
| Git/browser integration | Git 与可复现安装的 Playwright 浏览器另层 | 规划机 Git `2.49.0.windows.1`；CI checksum 固定 MinGit `2.55.0.windows.3`；lockfile 固定 Playwright `1.61.1`，Chromium 已安装并通过本机 smoke |
| Credentialed live | Claude/Codex CLI 与凭据是 release gate，不冒充 core | Claude Code `2.1.211`，Codex CLI `0.144.0` |
| SQLite | P0 只冻结现状；B0 另做 driver/prebuild 裁决 | Node `node:sqlite` = `3.51.2`（低于计划门 `3.51.3`，不可作迁移基线）；Python = `3.50.4`；sqlite3 CLI = `3.41.2` |

Git 全局 `core.autocrlf=true`。仓库新增精确路径 `.gitattributes`，只把 contracts/daemon 的四个 generated `*.ts` 固定为 LF，避免 Windows `pnpm gen` 后出现内容 diff 为零但 status 变脏；不做全仓换行符重写。

## 3. 源码/入口清单基线

| 项 | 精确数 |
|---|---:|
| Git tracked 文件（制品基线） | 520 |
| Python `.py` | 216 |
| `apps/server` Python | 118 |
| `apps/daemon` Python | 48 |
| `packages/contracts` Python | 19 |
| `apps/mock-server` Python | 5 |
| `scratchpad` Python | 23 |
| `scripts` Python | 3 |
| `.mjs` | 23 |
| `.ps1` | 1 |
| tracked `.js/.cjs/.sh/.bat/.cmd/.psm1` | 0 |
| tracked CI workflow | 1 |

机器权威是 `migration-inventory.json` + `pnpm verify:migration-inventory`；它还盘点 shebang、git executable bit、package `scripts/bin`、pyproject entry point、文档命令与 CI `run` 入口，不只按扩展名白名单计数。tracked 文件内容 fingerprint 取 baseline Git blob payload 的 SHA-256，避免 Windows checkout 的 CRLF smudge 改变证据；扫描范围 SHA-256 为 `5e759b580c9e99f6dc7063246d637fb71cf200891df0df28e6ccd07011eb5188`。

本轮 scanner 共冻结 `449` 项：`244 file`、`144 doc-command`、`35 package-script`、`20 ci-run`、`1 ci-workflow`、`1 inline-script`、`1 package-bin`、`3 pyproject-script`。处置为 `146 keep / 253 replace / 50 retire`，每项均有 owner、理由、内容 fingerprint 与适用时的目标阶段/目标实现。

## 4. 测试 oracle 基线

| 子系统 | pytest collection |
|---|---:|
| server | 668 |
| daemon | 230 |
| contracts | 71 |
| mock-server | 12 |
| **合计** | **981** |

- 981 个唯一 pytest nodeid 的 canonical JSON 排序 SHA-256：`ba4c125821736c1be97e6f6d4b77c99ad060c4d87305e8bb2b5895f7c1949271`。
- 不可变源集合：`oracle-collection.json`。
- 可演进映射：`test-ledger.json`；P0 `pending` 不伪造 TS target，但必须与 oracle 一一对齐。默认 strict/波次门必须拒绝 pending、缺 target、未批准 retire 或非独立 reviewer。
- Vitest JSON reporter 冻结目标集合：web `266 passed / 47 files`，daemon-ts `270 passed + 4 skipped / 23 files`，合计 `540` 个 target ID；清单 SHA-256 为 `721b57aaf13f40d52b56868141698db43b8219aa51972f0d0d01605bf765daf6`，见 `ts-test-collection.json`。

## 5. API/数据库迁移面

`api-db-inventory.json` 继续绑定产品执行起点 `cee5d98577e81c43264db15ebcb24cdbdcfcf436`，因为 P0 实现提交未修改产品 API、ORM 或迁移表面。

| 面 | P0 精确数 | 说明 |
|---|---:|---|
| product REST catalog | 80 | contracts `ENDPOINTS_*` 去重后口径 |
| server HTTP decorators | 82 | 80 product + health + favicon |
| WS decorators | 2 | browser WS + daemon WS |
| ORM 业务表 | 35 | `Base.metadata` 口径；raw FTS 虚表不在 metadata |
| Alembic revisions | 13 | 单 head `0013_b1_suggested_owner` |

P1 将从本清单冻结 operation 全字段、WS 帧/时序、DDL/触发器/索引/FTS 与每个历史 revision fixture；P0 不提前实施 P1。

## 6. 三层验证制度

1. **Hermetic core**：全新 Windows runner 只安装 Node/pnpm，跑 TS 生成/静态/测试/构建；不得命中 Python/node-gyp。
2. **Git/browser integration**：显式安装声明版本 Git 与 Playwright browser，跑真 git/浏览器集成。
3. **Credentialed live**：受控环境使用已登录 Claude/Codex CLI；凭据不进普通 CI，但 release 前不可永久 skip。

三层证据不得互相替代。P0 建立 Windows CI 骨架与本地等价命令；外部 runner 实跑必须记录 workflow run URL/SHA，未 push 时不得标绿。

## 7. 本地守门结果

> 以下均为 2026-07-20 在 P0 实现提交与对应冻结制品上实跑；外部 Windows runner 结果仍单独 pending。

| 命令 | 结果 | 状态 |
|---|---|---|
| `uv run pytest -q` | `977 passed / 4 skipped` | ✅ |
| `pnpm -F @coagentia/web test` | `266 passed / 47 files`；保留既有 happy-dom localhost 噪声，退出码 0 | ✅ |
| `pnpm -F @coagentia/daemon-ts test` | `270 passed / 4 skipped / 23 files` | ✅ |
| daemon hermetic core | `244 passed / 4 skipped / 20 files` | ✅ |
| `pnpm typecheck` | 三个 tsc + pyright `0 errors` | ✅ |
| `uv run ruff check .` | `All checks passed` | ✅ |
| `pnpm gen` 双跑 + zero diff | 两次制品 SHA-256 一致，生成路径 tracked diff 为 0 | ✅ |
| `pnpm install --frozen-lockfile` | lockfile up to date | ✅ |
| `pnpm -F @coagentia/web build` | Vite build 通过；保留既有 chunk-size warning | ✅ |
| `pnpm p0:integration:smoke` | 真 Git Unicode/空格路径 commit + Playwright Chromium | ✅ |
| `pnpm verify:oracle-collection` | 981 项与冻结 oracle 精确一致 | ✅ |
| `pnpm verify:p0` | tools 31/31；target mutants 8/8；ledger mutants 45/45；inventory mutants 18/18；authority mutants 39/39 | ✅ |

首次外部 fresh-run 为 [GitHub Actions #29798911693](https://github.com/catbobyman/CoAgentia/actions/runs/29798911693)，绑定 SHA `7988a923308974287036869f0de61d94c72d1fb5`。该 run 暴露 PowerShell 7 guard 编译和 merge history 审计两项缺陷，如实保留为红线证据。

第二次 fresh-run 为 [GitHub Actions #29800210719](https://github.com/catbobyman/CoAgentia/actions/runs/29800210719)，绑定 SHA `82f6f82b26d3440bb05981f905d7d4a9ca81d338`。native deny guard、legacy 回归、生成与 build 已通过；hermetic job 实证 fresh clone 没有 ignored `build/*.json`，legacy 母账则实证 Windows `core.autocrlf` 下四个 generated 文件出现 zero-diff/status-dirty。`f90d423` 以 tracked P0 校准快照重放生成、比较 committed→first→second 四文件哈希，并用精确 `.gitattributes` 固定这四类输出为 LF；独立复核为 Critical/Major/Minor = 0。

第三次 fresh-run [GitHub Actions #29801764794](https://github.com/catbobyman/CoAgentia/actions/runs/29801764794) 绑定 SHA `c754ceb60d9056257e180dc1a78e6b7a258bde27` 并全绿：Hermetic core `2m18s`、Git + Chromium integration `3m42s`、Legacy Python oracle `9m56s`。三份 P0 generator inputs 仅作校准快照；正式 schema/OpenAPI 冻结仍归 P1。

## 8. P0 验收表

| ID | 结果 | 证据 |
|---|---|---|
| TS-P0-01 基准/dirty/tests 可解释 | **accepted** | 本文 + 试验裁决 + 981 oracle |
| TS-P0-02 全入口与 inventory 一一对应 | **accepted** | 449 项 `migration-inventory.json` + verifier |
| TS-P0-03 全新 Windows runner 可复现 | **accepted** | [#29801764794](https://github.com/catbobyman/CoAgentia/actions/runs/29801764794) / `c754ceb` 三 job 全绿 |
| TS-P0-04 独立评审无漏入口/命令 | **accepted** | `docs/reviews/ts-migration/P0-REVIEW.md`；Critical/Major/Minor = 0 |
| TS-P0-05 test-ledger mutants 逐类被拒 | **accepted** | ledger 45/45 + target 8/8 + tooling 31/31 |
| TS-P0-06 `plan.md` authority 单源检查 | **accepted** | `PLAN-AUTHORITY.md` + scanner 39/39 mutants（含 P0→P1 排他状态边界） |

六项现已全部 reviewed/accepted，P0 完成。下一阶段只能按根 `plan.md` 进入 P1；本结论不授权提前进入 P2/A/B、发布 npm 或执行不可逆清场。
