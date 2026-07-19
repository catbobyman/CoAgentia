/**
 * fs.tree 扫描面（PS-WT，契约 D §6）：computer 级只读目录浏览（选仓库路径用）。
 *
 * 对等基准 = apps/daemon client.py 的 `_fs_root_entries` / `_fs_dir_entries` / `_fs_scan_entry`
 * / `_FS_TREE_MAX`（py 内联在 client.py；TS 拆独立模块供 client.ts（W4）复用——纯模块拆分，
 * 零行为变化，差异登记见 tests/pswt_fs_scan.test.ts 头注）。
 *
 * - 根视图：win32 逐盘符 A:–Z: 探测（存在才列；exists 异常按 py OSError 口径视为不存在），
 *   posix 单条 "/"；
 * - 子层：**仅列子目录**（跳过文件），按名排序，超 FS_TREE_MAX 截断；
 * - 逐条降级：类型判定异常 → denied 标记但仍列出，绝不吞条目；整层打不开 → 空层不炸；
 * - 永不读文件内容；指向目录的 junction/符号链接列为目录（单层查询天然无递归，只列不跟）。
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

import type { FsTreeEntry } from '@coagentia/contracts-ts';

/** 契约 D §6 / PS-WT §4：fs.tree 单层目录上限，超出截断（py `_FS_TREE_MAX`）。 */
export const FS_TREE_MAX = 500;

const DRIVE_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

/**
 * 单条目扫描的最小结构面（`fs.Dirent` 结构兼容；py 对应 `os.DirEntry`）。
 * 测试可用伪条目覆盖 denied 降级路径（py `_FakeEntry` 同款）。
 */
export interface ScanDirent {
  readonly name: string;
  readonly parentPath: string;
  isDirectory(): boolean;
  isSymbolicLink(): boolean;
}

/** fs.tree 根视图：win32 逐盘探测（py 3.11 无 os.listdrives 的同款手写），posix 单条 "/"。 */
export function fsRootEntries(): FsTreeEntry[] {
  if (process.platform === 'win32') {
    const entries: FsTreeEntry[] = [];
    for (const letter of DRIVE_LETTERS) {
      const root = `${letter}:\\`;
      let present: boolean;
      try {
        present = fs.existsSync(root);
      } catch {
        // py 侧 Path.exists 包 OSError → 视为不存在（node existsSync 实际不抛，保留对等护栏）
        present = false;
      }
      if (present) entries.push({ name: root, path: root, has_git: false, denied: false });
    }
    return entries;
  }
  return [{ name: '/', path: '/', has_git: false, denied: false }];
}

/**
 * 列 target 下**仅子目录**（跳过文件），按名排序；逐条 try/catch 探测 has_git/denied，
 * 一个坏目录不炸整层；超 FS_TREE_MAX 截断。永不读文件内容；符号链接/junction 只列不跟。
 */
export function fsDirEntries(target: string): [FsTreeEntry[], boolean] {
  let dirents: fs.Dirent[];
  try {
    dirents = fs.readdirSync(target, { withFileTypes: true });
  } catch {
    // 整层打不开（权限/IO/不存在）→ 空层；父层已可就该目录标 denied。
    return [[], false];
  }
  const rows: FsTreeEntry[] = [];
  for (const entry of dirents) {
    const row = fsScanEntry(entry);
    if (row !== null) rows.push(row);
  }
  rows.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  if (rows.length > FS_TREE_MAX) return [rows.slice(0, FS_TREE_MAX), true];
  return [rows, false];
}

/** 单条目：文件 → null（不列）；目录 → FsTreeEntry；探测异常 → denied 降级但仍出现。 */
export function fsScanEntry(entry: ScanDirent): FsTreeEntry | null {
  const full = path.join(entry.parentPath, entry.name);
  let isDir: boolean;
  try {
    if (entry.isDirectory()) {
      // Dirent 用父层列举信息（无需 stat 子目录本身；py DirEntry.is_dir 同款缓存面）。
      isDir = true;
    } else if (entry.isSymbolicLink()) {
      // py DirEntry.is_dir(follow_symlinks=True)：指向目录的 junction/符号链接列为目录
      // （单层查询天然无递归，只列不跟）；node Dirent 不跟链，故补一次 stat 跟链判型。
      isDir = statIsDir(full);
    } else {
      isDir = false;
    }
  } catch {
    // 类型判定失败（reparse/IO/权限）→ 保守列出并标 denied，绝不吞掉该条。
    return { name: entry.name, path: full, has_git: false, denied: true };
  }
  if (!isDir) return null;
  let denied = false;
  let hasGit: boolean;
  try {
    hasGit = fs.existsSync(path.join(full, '.git')); // worktree 的 .git 是文件也算
  } catch {
    hasGit = false;
    denied = true;
  }
  return { name: entry.name, path: full, has_git: hasGit, denied };
}

/** 跟链判型：断链（ENOENT）按 py「FileNotFoundError 吞掉」口径视为非目录；其余异常上抛走 denied。 */
function statIsDir(full: string): boolean {
  try {
    return fs.statSync(full).isDirectory();
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return false;
    throw err;
  }
}
