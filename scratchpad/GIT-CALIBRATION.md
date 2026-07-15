# M6 J3 Git 实操校准（Windows）

| 项 | 结论 |
| --- | --- |
| 状态 | ✅ 2026-07-11 真机脚本三次稳定复跑，最终 10/10 探针通过 |
| 环境 | Windows NT 10.0.26200.0 / Windows PowerShell 5.1.26100.8655 / Git for Windows 2.49.0.windows.1 |
| 隔离 | 每次在 `%TEMP%\coagentia-m6-git-校准-<随机>` 创建中文路径 scratch 仓库；未以 CoAgentia 仓库为靶子；结束删除整个临时根 |
| 复现脚本 | `scratchpad/run-git-calibration.ps1` |

## 1. 校准方法

脚本通过 `System.Diagnostics.ProcessStartInfo` 直接启动 `git.exe`，不经 shell 拼接参数；所有子进程都显式设置 stdout/stderr 为 UTF-8，并关闭交互输入。占用文件的校准子进程由 `taskkill /F /T /PID` 收尾。覆盖：

1. 原生反斜杠、正斜杠、中文路径的 `worktree add/remove/list`。
2. `git worktree lock` 与 `.git/index.lock`。
3. 独占文件句柄导致的 `worktree remove --force` 删除失败。
4. 151–299 字符绝对路径矩阵。
5. `merge --no-ff` 成功、内容冲突、冲突文件采集和 `merge --abort`。
6. 增/删/改/重命名/二进制/中文文件/CRLF 的 `diff --name-status`、`--numstat`、`-p`，含 `-z` 机器格式。

本机系统 Git 配置中 `core.autocrlf=true`，`core.longpaths` 未设置。校准仓库将 `core.autocrlf=false` 固定为确定性基线，另用 CRLF 文件验证 patch 行为。

## 2. 结果总表

| 探针 | 真实结果 | 实现约束 |
| --- | --- | --- |
| 反斜杠 worktree 路径 | add/remove 均为 0 | argv 可接原生路径 |
| 正斜杠 worktree 路径 | add/remove 均为 0 | 两种分隔符均可；内部仍统一规范化 |
| 中文仓库/树/提交 | UTF-8 显式解码后完整可读 | 不使用默认 GBK/GB2312 解码 |
| `worktree lock` | remove 退出 128；unlock 后 remove 为 0 | 锁视为可重试失败，不静默覆盖 |
| `.git/index.lock` | 写操作退出 128；移除校准锁后恢复 | 不得把锁存在误判成仓库损坏 |
| 文件被进程独占 | remove 退出 255，目录残留，但 worktree 登记已消失 | cleanup 必须同时检查 Git 登记与物理目录 |
| 长路径矩阵 | 151/172/193 成功；214/235/256/277/299 均失败 | `core.longpaths=true` 也不是 worktree 长根路径保证 |
| `merge --no-ff` | 成功提交恰有 2 个 parent | 可用 parent 数验证非 fast-forward 合并 |
| 冲突 + abort | merge 退出 1；abort 后 HEAD/文件/clean 状态全部恢复 | abort 前先采集冲突文件 |
| diff 全形态 | name-status/numstat/patch 与 NUL 分隔均稳定 | 机器解析用 `-z`；不要拆人读 rename 文本 |

## 3. 关键行为与坑

### 3.1 路径、分隔符与长路径

- `git worktree list --porcelain` 无论输入正斜杠还是反斜杠，输出路径都规范为 `C:/...`。不能用未经规范化的原始字符串直接比对 worktree 路径。
- 中文路径与空格路径在显式 UTF-8 解码下正常；`core.quotepath=true` 时中文文件名会成为八进制转义，`core.quotepath=false` 才保留可读 UTF-8。
- 本次临时根长度下，worktree 绝对路径 193 字符成功，214 字符开始稳定报 `fatal: '$GIT_DIR' too big`；214–299 即使显式 `-c core.longpaths=true` 也全部失败。该阈值只代表本机组合，不应硬编码成业务规则。
- 契约路径 `worktrees/<project_id>/<task_id>/` 必须保持短组件，不得混入 Project 名、任务标题或分支名。默认数据根下的 ULID 路径远低于本次失败区间；Git 若仍报长路径错误，按执行失败上报，不发明新错误码或隐式改路径。
- Windows PowerShell 5.1 的 `Remove-Item -Recurse` 对超长子目录可能清理失败；Git 对象又可能带只读属性。校准脚本的兜底先验证目标仍在 `%TEMP%/coagentia-m6-git-*`，再用 `\\?\` 扩展路径清属性并递归删除，最后强断言临时根不存在。最终复跑确认残留数为 0。

### 3.2 锁文件与进程占用

`git worktree lock --reason "M6 calibration"` 后，remove 返回：

```text
fatal: cannot remove a locked working tree, lock reason: M6 calibration
use 'remove -f -f' to override or unlock first
```

校准确认锁标记位于主仓库 `.git/worktrees/<name>/locked`。cleanup 不应默认用双 `--force` 越过显式锁。

人为创建 `.git/index.lock` 后，`git add` 退出 128，并提示可能有另一个 git 进程。产品代码不得看到 `index.lock` 就自行删除；应保留 stderr 诊断并允许稍后重试。

独占 worktree 内文件句柄后，`git worktree remove --force` 退出 255：

```text
error: failed to delete '.../worktrees/进程占用树': Invalid argument
```

此时出现重要的半完成态：**物理目录仍存在，但 `worktree list --porcelain` 已不再登记该树**。因此 cleanup 幂等不能只重跑 `git worktree remove`：

1. 先检查 Git 登记，存在则执行 remove。
2. 再检查物理目录，仍存在则在占用解除后做受限目录清理。
3. 最后 `git worktree prune`，并仅在登记和目录都消失后上报 `cleaned`。

校准脚本只杀死自己创建的占用进程；daemon 不得为清理 worktree 盲杀未知用户进程。

### 3.3 `merge --no-ff` 与冲突恢复

- 非冲突分支执行 `git merge --no-ff <branch> -m <message>` 成功，生成新 HEAD，`rev-list --parents -n 1 HEAD` 显示两个 parent。
- 内容冲突时 Git 退出 1，冲突说明出现在 stdout（不能只读 stderr），`.git/MERGE_HEAD` 存在。
- `git diff --name-only --diff-filter=U` 在 abort 前稳定给出 `conflict.txt`。
- `git merge --abort` 后：HEAD 等于合并前 HEAD、主干内容恢复、`MERGE_HEAD` 消失、`status --porcelain` 为空。

实现顺序固定为：记录主干 HEAD → merge → 若失败先采集 `U` 文件 → abort → 验证 HEAD/状态恢复 → 上报 conflicted。不能在采集冲突文件之前 abort。

### 3.4 Diff 的真实输出

`core.quotepath=false` 下，本次 `--numstat --find-renames` 为：

```text
1       0       added.txt
-       -       binary.bin
1       1       crlf.txt
0       1       delete.txt
2       1       modify.txt
0       0       rename-old.txt => rename-new.txt
2       1       中文 文件.txt
```

注意事项：

- 二进制 numstat 是 `-/-`，普通 `git diff -p` 会输出 `Binary files ... differ`。协议要求二进制 `additions=0/deletions=0/patch=""`，实现必须显式映射，不能把人读提示当 patch。
- 非 `-z` 的重命名 numstat 是 `old => new`，路径本身也可能含相同文本，不能靠字符串切分。
- `--numstat -z` 的重命名记录真实形状为 `0\t0\t<NUL>old<NUL>new<NUL>`；`--name-status -z` 为 `R100<NUL>old<NUL>new<NUL>`。增删改记录则以单一路径 NUL 结尾。daemon 应用 NUL 记录解析 status/path/old_path。
- `--name-status -z` 可提供 added/modified/deleted/renamed；`--numstat -z` 提供计数；逐文件 `-p --no-color --find-renames` 提供 unified patch。三者职责不要混成一个脆弱解析器。
- CRLF 文件只显示目标行 1 增 1 删，但 patch 内容行保留 `\r`；patch 截断应基于 UTF-8 字节上限，避免在多字节字符中间截断。
- 文件列表截断前先算全量 totals；逐文件 patch 截断与 200 文件截断是两个独立层级。

### 3.5 输出编码

- Git 重定向 stdout 的中文提交主题用 UTF-8 解码为 `差异提交：增删改重命名与二进制`。
- 同一组 UTF-8 字节按本机默认 GB2312 解码会变成 `宸紓...`。所有 git/检查命令子进程必须显式 `encoding="utf-8"`，stderr 同样处理。
- Windows PowerShell 5.1 会把**无 BOM** 的 UTF-8 `.ps1` 当 ANSI 解析。本校准脚本因此保存为 UTF-8 BOM；Python 产品代码不受该脚本解析坑影响。

## 4. J3/J4/J5 实现清单

- 使用参数数组启动进程，`shell=False`，stdout/stderr 显式 UTF-8；不拼 `cmd /c`。
- 只对 daemon 自己启动且需强制收尾的进程使用 `taskkill /F /T`。
- 路径先 `resolve`/规范化并约束在预期 repo/worktree 根；比较时统一路径语义，不比较 Git 的原始斜杠文本。
- ensure 的自然键是 task_id；已存在时 noop 并回报现状。
- cleanup 对 Git 登记、物理目录、prune 三面幂等，覆盖 remove 半完成态。
- merge 由 server 给 DAG 序与 message；daemon 只执行 `--no-ff`，冲突文件在 abort 前采集。
- Diff 机器元数据使用 `-z`，patch 使用无颜色 unified diff；二进制和三级截断按 D §6 映射。

## 5. 复跑

```powershell
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File scratchpad\run-git-calibration.ps1
```

脚本最终输出 JSON；顶层 `passed=true` 才算通过。2026-07-11 最终复跑 10 个探针全部 `passed=true`，临时根已由 `finally` 清理。
