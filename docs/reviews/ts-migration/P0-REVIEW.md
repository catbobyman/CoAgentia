# P0 独立评审记录

> 评审日期：2026-07-20
> 首个实现：`27c044dfdf97283f7618a6d454a1499fe8e75fec`
> 最终工具/CI 修复基线：`f90d4234bc4303ed4980d19898adca50ed804f8a`
> 冻结制品基线：`a2fa540036be69a79774d1b5f446fd02bd5935f3`
> 执行规则来源：[仓库根 plan.md](../../../plan.md)
> 评审结论：PASS（Critical = 0，Major = 0）
> 阶段结论：P0 PASS（reviewed/accepted）；`TS-P0-01`～`TS-P0-06` 全部闭环

## 1. 评审范围

本轮由独立子代理分别覆盖三组风险面：P0 总门禁与 authority 拓扑、hermetic core 隔离、inventory/文档命令入口。评审者不修改受评代码；最终结论基于已暂存实现树、冻结制品与实际命令输出。

执行期间未加载或调用 Coze CLI。

## 2. 对抗性复查结果

| 风险面 | 最终证据 | 结论 |
|---|---|---|
| evidence-only 历史约束 | 从实现基线到 HEAD 逐提交检查；源码改动后回滚仍被报告，dirty worktree 被拒绝 | PASS |
| `plan.md` authority 拓扑 | Markdown title/angle/fragment、YAML fragment、plain `.md`、命名替代计划、root/self camouflage、中英文替代计划等 35 类 mutant 全部被拒 | PASS |
| test ledger/target | skipped target 不得伪装 passed；target 8/8、ledger 45/45 mutants 全部被拒 | PASS |
| inventory 完整性 | 多后缀、shebang/executable、package scripts/bin、CI、Markdown/Pandoc/PowerShell/Git alias 等入口覆盖；tracked fingerprint 绑定 Git blob canonical payload；18/18 mutants 被拒 | PASS |
| hermetic core | 删除 `NODE_OPTIONS` 的 child/fork 仍回注 guard；Worker、隐藏 shell Git、绝对 Git 路径被拒；允许 Vite `net use` | PASS |
| workflow 可复现性 | 3 个 Windows job 均为 checkout v6 + `fetch-depth: 0`；Python 固定 `3.14.0`；Node/pnpm/MinGit/Playwright 固定版本或校验和 | PASS |
| 首次 fresh-run 修复 | PowerShell 5.1 编译 native exit-86 guard；first-parent 结果树审计；history-only merge、merge 带入内容及 rename 伪装回归 | PASS |
| 第二次 fresh-run 修复 | tracked P0 generator inputs；四生成物 committed→first→second 哈希；generated `*.ts` 精确 LF 属性；母账前 status 诊断 | PASS |

评审过程中识别出的 authority 伪装、文档命令漏扫、Git alias/间接 shell、child/Worker 隔离等问题均已修复，并转化为冻结回归 mutant。证据收口复核另发现 2 个产品 operationId 错取 mock 值、2 个迁移哈希受 CRLF 工作树字节影响，以及 tracked workflow fingerprint 会随 `core.autocrlf=true` fresh checkout 漂移；前两项已改为真实 server OpenAPI 与 baseline Git blob 的值，后一项已改为批量读取 baseline Git blob payload，并由 CRLF clone、binary payload、dirty/untracked fail-closed 回归锁定。首次证据提交后，live authority gate 又发现两份证据的措辞会被解读成替代计划声明，现已改为带根计划链接的中性验收表述。最终复审未留下 Critical 或 Major。

owner 授权首次 push 后，[GitHub Actions #29798911693](https://github.com/catbobyman/CoAgentia/actions/runs/29798911693) 实证 PowerShell 7 无法输出 console `Add-Type` assembly，且 history-only merge 会被旧 `diff-tree -m` 按第二父提交展开。修复改用绝对路径 Windows PowerShell 5.1 编译 guard，并显式以 `commit^1` 比较每个 first-parent 结果树。独立对抗复核先指出原 fixture 没有真正复现多父误报，收紧后进一步发现 `git diff --name-only` 的 rename detection 可把非证据源路径伪装进 evidence prefix；最终实现强制 `--no-renames`，并以“无树变化 ours merge”和“README rename 进 evidence 后由 merge 带入”双 fixture 锁定允许/拒绝两面。最终复核为 Critical = 0、Major = 0、Minor = 0，reviewer 未修改受评文件。

[GitHub Actions #29800210719](https://github.com/catbobyman/CoAgentia/actions/runs/29800210719) 进一步实证 hermetic fresh clone 缺少 ignored `build/*.json`，且 Windows `pnpm gen` 后四个生成物在内容 diff 为零时仍因行尾被 `git status` 标脏。修复新增三份与基线 SHA-256 一致的 tracked P0 校准快照，README 明示它们不是契约或 P1 schema/OpenAPI 权威；hermetic job 在 Git/Python deny 后复制快照并覆盖比较四个生成物。`.gitattributes` 只对两个 generated 目录的 `*.ts` 强制 LF，不放宽任何 clean/diff gate。第二轮独立复核同样为 Critical = 0、Major = 0、Minor = 0，reviewer 未修改受评文件。

## 3. 实证摘要

- P0 tooling tests：`31/31`。
- daemon hermetic core：`244 passed / 4 skipped / 20 files`。
- daemon 全量：`270 passed / 4 skipped / 23 files`。
- web：`266 passed / 47 files`。
- Python oracle：`977 passed / 4 skipped`；冻结 collection 为 `981`。
- target/ledger/inventory/authority mutants：`8/8`、`45/45`、`18/18`、`39/39`。
- Git + Chromium integration smoke：PASS。
- `pnpm typecheck`、`uv run ruff check .`、`pnpm gen` 双跑 zero diff、frozen install、web build：PASS。

## 4. 阶段结论与后续边界

`TS-P0-03` 已由 [GitHub Actions #29801764794](https://github.com/catbobyman/CoAgentia/actions/runs/29801764794) 闭环，绑定 SHA `c754ceb60d9056257e180dc1a78e6b7a258bde27`：Hermetic core、Git+Chromium integration、Legacy Python oracle 三 job 全绿。结合两轮独立复核 Critical/Major/Minor = 0，P0 六项验收全部 reviewed/accepted。

后续边界：

1. P1 前置已满足，可按根计划另起阶段任务书与证据。
2. 不越过 P1/P2-cal/P2 依赖提前进入 A 或 B 波次。
3. P0 校准快照不取得契约权威；P1 负责正式冻结 schema/OpenAPI。
