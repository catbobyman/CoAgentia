# P0 可复现基线

> 状态：IN PROGRESS
> 阶段：P0「基线、范围和当前试验裁决」
> 执行规则来源：[仓库根 plan.md](../../../plan.md)
> 计划代码基准：`54f1372`
> P0 产品执行起点：`cee5d98577e81c43264db15ebcb24cdbdcfcf436`
> P0 工具/证据制品基线：`238aef645338ca4fe105c6a2d913a64a99343551`
> 制品基线 tree：`7d43354ee04ba150b522cd46b98074882b457174`
> 当前执行分支：`main`
> 证据日期：2026-07-20

## 1. 基线边界

- owner 已批准仓库根 `plan.md` v1.0 的「全仓纯 TypeScript / `.py=0` / TS 契约单源」终态。
- P0 不修改产品契约，不删除 Python，不切默认 server/daemon，不改生产数据库。
- `cee5d98577e81c43264db15ebcb24cdbdcfcf436` 是产品表面的执行起点；P0 工具、workflow 与 authority 首个实现提交为 `27c044dfdf97283f7618a6d454a1499fe8e75fec`，fresh-checkout fingerprint 修复提交为 `a7de995590e9d59cae6d4a9df02fdc99627e8c3a`。首次外部 Windows runner 又实证出 PowerShell 7 不支持 console `Add-Type` 输出及 history-only merge 被 `diff-tree -m` 误展开两项机制缺陷，最终修复提交为 `238aef645338ca4fe105c6a2d913a64a99343551`；当前冻结 JSON 制品统一绑定该提交。
- 开工前的 7 处 daemon 分发/退役试验已原样冻结于本地分支 `codex/p0-daemon-distribution-spike`（`96492b8`），不计入绿基线；裁决见 [P0-EXPERIMENT-DECISIONS.md](P0-EXPERIMENT-DECISIONS.md)。
- 未授权 push/npm 发布/外部数据写入；P0 的 GitHub Actions 已落本地 workflow，新鲜 Windows runner 结果需后续明示 push 授权。

## 2. 支持矩阵与规划机环境

| 层级 | P0 声明 | 规划机实况 |
|---|---|---|
| OS/CPU | Windows 11 x64，AMD64/x64 为当前必过矩阵 | Windows 11 Home `10.0.26200` / Ryzen 9 8945HS（8C/16T） |
| Hermetic core | Node 22 + pnpm；不得调用 Python/node-gyp | Node `22.22.1`，pnpm `10.6.2` |
| Legacy oracle | Python/uv 仅在清场前作冻结 oracle | Python `3.14.0`，uv `0.9.0` |
| Git/browser integration | Git 与可复现安装的 Playwright 浏览器另层 | 规划机 Git `2.49.0.windows.1`；CI checksum 固定 MinGit `2.55.0.windows.3`；lockfile 固定 Playwright `1.61.1`，Chromium 已安装并通过本机 smoke |
| Credentialed live | Claude/Codex CLI 与凭据是 release gate，不冒充 core | Claude Code `2.1.211`，Codex CLI `0.144.0` |
| SQLite | P0 只冻结现状；B0 另做 driver/prebuild 裁决 | Node `node:sqlite` = `3.51.2`（低于计划门 `3.51.3`，不可作迁移基线）；Python = `3.50.4`；sqlite3 CLI = `3.41.2` |

Git 全局 `core.autocrlf=true`，仓库当前无 `.gitattributes`。P0 将此记为可复现环境事实，不在基线批做全仓换行符重写。

## 3. 源码/入口清单基线

| 项 | 精确数 |
|---|---:|
| Git tracked 文件（制品基线） | 507 |
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

机器权威是 `migration-inventory.json` + `pnpm verify:migration-inventory`；它还盘点 shebang、git executable bit、package `scripts/bin`、pyproject entry point、文档命令与 CI `run` 入口，不只按扩展名白名单计数。tracked 文件内容 fingerprint 取 baseline Git blob payload 的 SHA-256，避免 Windows checkout 的 CRLF smudge 改变证据；扫描范围 SHA-256 为 `d8fcfe71d50f88a573c161ff1c4a8de3c6e66c456bb84335466718d43a636a15`。

本轮 scanner 共冻结 `447` 项：`243 file`、`144 doc-command`、`35 package-script`、`19 ci-run`、`1 ci-workflow`、`1 inline-script`、`1 package-bin`、`3 pyproject-script`。处置为 `145 keep / 252 replace / 50 retire`，每项均有 owner、理由、内容 fingerprint 与适用时的目标阶段/目标实现。

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
| `pnpm verify:p0` | tools 30/30；target mutants 8/8；ledger mutants 45/45；inventory mutants 18/18；authority mutants 35/35 | ✅ |

首次外部 fresh-run 为 [GitHub Actions #29798911693](https://github.com/catbobyman/CoAgentia/actions/runs/29798911693)，绑定 SHA `7988a923308974287036869f0de61d94c72d1fb5`。该 run 的 legacy 回归、生成、build 均通过，但 hermetic guard 在 PowerShell 7 `Add-Type -OutputType ConsoleApplication` 处失败，随后 P0 history gate 又把无文件变化的远端历史 merge 按第二父提交误判成全仓漂移；因此该 run 如实保留为红线证据，不计作 `TS-P0-03` 通过。修复后的 fresh-run 仍待取得。

## 8. P0 验收表

| ID | 结果 | 证据 |
|---|---|---|
| TS-P0-01 基准/dirty/tests 可解释 | 本地通过 | 本文 + 试验裁决 + 981 oracle |
| TS-P0-02 全入口与 inventory 一一对应 | 本地通过 | 447 项 `migration-inventory.json` + verifier |
| TS-P0-03 全新 Windows runner 可复现 | 首跑红，修复待复跑 | 首跑 [#29798911693](https://github.com/catbobyman/CoAgentia/actions/runs/29798911693) 暴露两项 CI 机制缺陷；`238aef6` 已修复并补回归，仍需新的全绿 run URL/SHA |
| TS-P0-04 独立评审无漏入口/命令 | 本地通过 | `docs/reviews/ts-migration/P0-REVIEW.md`；Critical/Major = 0 |
| TS-P0-05 test-ledger mutants 逐类被拒 | 本地通过 | ledger 45/45 + target 8/8 + tooling 30/30 |
| TS-P0-06 `plan.md` authority 单源检查 | 本地通过 | `PLAN-AUTHORITY.md` + scanner 35/35 mutants |

P0 只在六项全部 reviewed/accepted 后改为完成；未授权的外部 CI 跑次是当前唯一明示门，不会被本地绿线替代，也不会提前进入 P1/A/B。
