/**
 * PS-WT fs.tree 只读浏览（对等基准 = apps/daemon tests/test_pswt_fs_scan.py 的 fs.tree 面）。
 *
 * 对等口径：py 文件 21 例中 fs.tree 面 9 例归本文件；worktree.scan / 孤儿清理护栏 12 例依赖
 * GitWorktreeManager（git.ts，TS-W2 其它模块）不在此移植。差异登记：
 * - py test_fs_root_win32_lists_present_drives 用 monkeypatch 伪造盘面（{C:\, D:\}）断言精确列表；
 *   TS 直译（fsRootEntries 无注入缝，与 py 签名一致）→ 按平台 it.skipIf 用**真实盘面**断言
 *   形状 / 升序 / 系统盘命中 / 标志位；
 * - py test_fs_root_posix_single_slash monkeypatch sys.platform='linux'；TS 按平台 it.skipIf
 *   （win32 主机上跳过，posix 主机上实跑）；
 * - py test_handle_query_fs_tree_reply 依赖 DaemonClient.handle_query（client.ts，TS-W4 未建）
 *   → 延后到 W4 client 测试对等补入，本文件不移植；
 * - TS 补充一例 junction/符号链接面（py 无对应用例，仅源码注释语义）：node Dirent 不跟链，
 *   fsscan.ts 补 stat 跟链判型，需测试锚守住「指向目录的链列为目录、断链不列」。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { FS_TREE_MAX, fsDirEntries, fsRootEntries, fsScanEntry } from '../src/fsscan.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-fsscan-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

// ---------------------------------------------------------------- fs.tree：根视图

describe('fs.tree 根视图', () => {
  // py test_fs_root_win32_lists_present_drives（差异：真实盘面口径，见头注）
  it.skipIf(process.platform !== 'win32')('win32 逐盘符 A:–Z: 探测，仅列存在盘符', () => {
    const entries = fsRootEntries();
    expect(entries.length).toBeGreaterThan(0);
    for (const e of entries) {
      expect(e.path).toMatch(/^[A-Z]:\\$/);
      expect(e.name).toBe(e.path);
      expect(e.has_git).toBe(false);
      expect(e.denied).toBe(false);
    }
    const drivePaths = entries.map((e) => e.path);
    expect(drivePaths).toEqual([...drivePaths].sort()); // 逐字母升序
    const sysDrive = (process.env['SystemDrive'] ?? 'C:').toUpperCase();
    expect(drivePaths).toContain(`${sysDrive}\\`); // 系统盘必在
  });

  // py test_fs_root_posix_single_slash（差异：win32 主机跳过，不 monkeypatch 平台）
  it.skipIf(process.platform === 'win32')('posix 根视图单条 "/"', () => {
    const entries = fsRootEntries();
    expect(entries.length).toBe(1);
    expect(entries[0]!.name).toBe('/');
    expect(entries[0]!.path).toBe('/');
    expect(entries[0]!.has_git).toBe(false);
    expect(entries[0]!.denied).toBe(false);
  });
});

// ---------------------------------------------------------------- fs.tree：子层列目录

describe('fs.tree 子层列目录', () => {
  // py test_fs_dir_lists_only_dirs_with_has_git
  it('仅列子目录且 has_git 命中（.git 目录与文件形式都算）', () => {
    const base = path.join(tmp, 'base');
    fs.mkdirSync(base);
    fs.writeFileSync(path.join(base, 'file.txt'), 'x', 'utf-8'); // 文件：不列
    fs.mkdirSync(path.join(base, 'plain')); // 普通目录：has_git=false
    const repoDir = path.join(base, 'repo');
    fs.mkdirSync(path.join(repoDir, '.git'), { recursive: true }); // 目录 .git
    const wtDir = path.join(base, 'worktree');
    fs.mkdirSync(wtDir);
    // worktree 的 .git 是文件（不是目录）也算命中。
    fs.writeFileSync(path.join(wtDir, '.git'), 'gitdir: /somewhere\n', 'utf-8');

    const [entries, truncated] = fsDirEntries(base);
    expect(truncated).toBe(false);
    expect(entries.map((e) => e.name)).toEqual(['plain', 'repo', 'worktree']); // 文件被跳过 + 按名排序
    const byName = new Map(entries.map((e) => [e.name, e]));
    expect(byName.get('plain')!.has_git).toBe(false);
    expect(byName.get('repo')!.has_git).toBe(true);
    expect(byName.get('worktree')!.has_git).toBe(true); // 文件形式的 .git 也算
    for (const e of entries) {
      expect(e.denied).toBe(false);
      expect(path.basename(e.path)).toBe(e.name); // path 为绝对子路径
    }
  });

  // py test_fs_dir_truncates_over_limit
  it('超 FS_TREE_MAX 截断', () => {
    const base = path.join(tmp, 'many');
    fs.mkdirSync(base);
    for (let i = 0; i < FS_TREE_MAX + 1; i += 1) {
      fs.mkdirSync(path.join(base, `d${String(i).padStart(4, '0')}`));
    }
    const [entries, truncated] = fsDirEntries(base);
    expect(truncated).toBe(true);
    expect(entries.length).toBe(FS_TREE_MAX);
  });

  // py test_fs_dir_exactly_at_limit_not_truncated
  it('恰 FS_TREE_MAX 条不截断', () => {
    const base = path.join(tmp, 'edge');
    fs.mkdirSync(base);
    for (let i = 0; i < FS_TREE_MAX; i += 1) {
      fs.mkdirSync(path.join(base, `d${String(i).padStart(4, '0')}`));
    }
    const [entries, truncated] = fsDirEntries(base);
    expect(truncated).toBe(false);
    expect(entries.length).toBe(FS_TREE_MAX);
  });

  // py test_fs_dir_unreadable_layer_is_empty_not_crash
  it('整层不可读（不存在）→ 空层不炸', () => {
    const missing = path.join(tmp, 'nope'); // 不存在 → readdir 抛 → 空层，不炸
    const [entries, truncated] = fsDirEntries(missing);
    expect(entries).toEqual([]);
    expect(truncated).toBe(false);
  });

  // TS 补充（py 无对应用例，仅 _fs_scan_entry 源码注释语义；node Dirent 不跟链故需测试锚）
  it('指向目录的 junction/符号链接列为目录条目，断链不列（只列不跟）', () => {
    const base = path.join(tmp, 'links');
    fs.mkdirSync(base);
    const real = path.join(tmp, 'outside-real');
    fs.mkdirSync(real);
    const gone = path.join(tmp, 'gone');
    fs.mkdirSync(gone);
    const linkType = process.platform === 'win32' ? 'junction' : null;
    fs.symlinkSync(real, path.join(base, 'link'), linkType);
    fs.symlinkSync(gone, path.join(base, 'broken'), linkType);
    fs.rmdirSync(gone); // 目标删除 → broken 成断链（py FileNotFoundError 吞掉口径 = 不列）

    const [entries, truncated] = fsDirEntries(base);
    expect(truncated).toBe(false);
    expect(entries.map((e) => e.name)).toEqual(['link']); // 断链 broken 不列
    expect(entries[0]!.has_git).toBe(false);
    expect(entries[0]!.denied).toBe(false);
    expect(fs.existsSync(real)).toBe(true); // 只列不跟：目标原样，无任何写副作用
  });
});

// ---------------------------------------------------------------- fs.tree：单条目扫描

/** 伪 Dirent：isDirectory 可被指定抛错以覆盖 denied 逐条降级（py _FakeEntry 对等）。 */
// erasableSyntaxOnly：构造器参数属性是非可擦除语法 → 显式字段 + 赋值。
class FakeEntry {
  readonly name: string;
  readonly parentPath: string;
  private readonly opts: { raiseIsDir?: boolean; isDir?: boolean };

  constructor(name: string, parentPath: string, opts: { raiseIsDir?: boolean; isDir?: boolean } = {}) {
    this.name = name;
    this.parentPath = parentPath;
    this.opts = opts;
  }

  isDirectory(): boolean {
    if (this.opts.raiseIsDir) throw new Error('拒绝访问');
    return this.opts.isDir ?? true;
  }

  isSymbolicLink(): boolean {
    return false;
  }
}

describe('fs.tree 单条目扫描', () => {
  // py test_fs_scan_entry_denied_on_probe_error
  it('类型探测异常 → denied 降级但仍出现（不吞条目）', () => {
    const entry = fsScanEntry(new FakeEntry('locked', tmp, { raiseIsDir: true }));
    expect(entry).not.toBeNull();
    expect(entry!.name).toBe('locked');
    expect(entry!.denied).toBe(true);
    expect(entry!.has_git).toBe(false);
  });

  // py test_fs_scan_entry_skips_files
  it('文件条目 → null（不列）', () => {
    expect(fsScanEntry(new FakeEntry('f', tmp, { isDir: false }))).toBeNull();
  });
});
