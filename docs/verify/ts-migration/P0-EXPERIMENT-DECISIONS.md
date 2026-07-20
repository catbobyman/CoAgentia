# P0 daemon 分发/退役试验裁决

> 裁决日期：2026-07-20
> 原始工作树：`cee5d98577e81c43264db15ebcb24cdbdcfcf436` + 7 处未提交试验
> 冻结分支：`codex/p0-daemon-distribution-spike`
> 冻结提交：`96492b8394c71ea74cdfc0e8361b252ebae4712a`
> 外部写入：无（未 push、未发布 npm 包）

## 总裁决

七处改动属 A1/A2/A3 的前置 spike，不是 P0 已完成能力。它们已原样保存在本地独立分支，`main` 回到七处试验之前的可重采基线。后续只能按下表在对应阶段拆分采纳，不得整批 cherry-pick 冒充完成。

| 文件 | 裁决 | 进入阶段 | 证据/理由 |
|---|---|---|---|
| `apps/daemon-ts/tsconfig.build.json` | adopt | A1 | `tsc` 构建通过，正确把相对 `.ts` import 重写为 `.js` |
| `apps/daemon-ts/src/adapters/cmdline.ts` | adopt | A1 | 源码态解析 `cli.ts`、构建态解析 `cli.js`；离仓 MCP 往返通过 |
| `apps/daemon-ts/tests/package_distribution.test.ts` | rework then adopt | A1 | pack→临时目录 install→bin→MCP 1/1 通过；须去掉未裁决包名/固定安装路径，补空格/Unicode 路径 |
| `apps/daemon-ts/package.json` | split/rework | A1 | `dist`/build/prepack/bin 方向可用；但公开包名、`private=false`、`publishConfig` 尚无 owner 分发渠道裁决 |
| `pnpm-lock.yaml` | regenerate later | A1 | 必须等 package 最终形状冻结后机械重生成，不单独采纳 |
| `apps/daemon-ts/tests/retirement.test.ts` | defer/rework | A3 | 当前 1/2 预期红；应改为显式 `verify:daemon-retired` 门，不混入观察期默认 vitest |
| `apps/server/tests/test_conformance_dual.py` | defer/rework | A2 | dual 展开的 mock/real 2/2 皆红；只能与真 server/mock 默认命令切换同批转绿 |

## 定向实证

| 检查 | 结果 |
|---|---|
| 离仓分发测试 | `1 passed` |
| daemon-ts 含两个试验文件直跑 | `272 passed / 4 skipped / 1 failed`，唯一失败为 Python daemon 尚未退役 |
| 试验态 server collection | `670`，比干净 server 基准 `668` 多 dual 的 2 例 |
| 试验态全仓 pytest collection | `983`，干净规划锚为 `981` |
| 未发布包可用性 | npm registry 查询 `coagentia-daemon` = `E404`；`npx --yes coagentia-daemon --version` 同样 `E404` |
| 旧官方 daemon 守门 | 包改名后 `pnpm -F @coagentia/daemon-ts test` 输出 `No projects matched`但退出码 `0`，属严重伪绿 |
| typecheck/build/frozen install | 试验分支均通过 |

## 后续强制门

1. A1 的任何包改名必须同批修正所有 filter/文档/CI，并让「无项目匹配」非零退出。
2. 默认命令不得指向未发布包；公开 npm、私有 registry 或签名离线包需 owner 另行裁决。
3. A2 只在真实可获取分发物存在后切 server/mock/conformance 默认命令。
4. A3 只在观察期和 C2 daemon 探针迁移通过后启用退役门。
