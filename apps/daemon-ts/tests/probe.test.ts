/**
 * runtime 探测（FR-2.3 / 契约 D §7；对等基准 = py test_probe.py，9 例逐条对应）。
 *
 * py→TS 移植登记（非行为改进）：
 * - 注入 runner=测试上下文 → codex 深探跳过真机 spawn（probe.ts 分支语义保留），全文件零子进程。
 * - symlink 例：win32 无特权时 'dir' symlink EPERM → 回退 'junction'（libuv 将 junction 报为
 *   isSymbolicLink，命中同一生产分支）；两者皆不可用才 skip（py 对应 pytest.skip）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { parseVersion, probeClaude, probeRuntimes, scanClaudeSkills } from '../src/probe.ts';

const runnerOk = async (_argv: string[]): Promise<[number, string, string]> => [
  0,
  '2.1.205 (Claude Code)',
  '',
];

const runnerFail = async (_argv: string[]): Promise<[number, string, string]> => [1, '', 'boom'];

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-probe-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe('probe（契约 D §7 runtimes.detected）', () => {
  it('claude 在装：runtime/installed/models/skills/version（test_probe_claude_installed）', async () => {
    const [rt, version] = await probeClaude(runnerOk, {
      which: (_n) => '/usr/bin/claude',
      skillsScan: () => ['docx', 'pdf'],
    });
    expect(rt.runtime).toBe('claude_code');
    expect(rt.installed).toBe(true);
    expect(rt.models !== undefined && rt.models.length > 0).toBe(true); // 模型列表非空
    expect(rt.skills).toEqual(['docx', 'pdf']); // 候选池扫描（契约 E v1.4 §9）
    expect(version).toBe('2.1.205');
  });

  it('未安装不扫技能（test_probe_claude_skills_empty_when_not_installed）', async () => {
    // 未安装 → 不扫技能（skillsScan 桩即便非空也不调用）。
    const [rt] = await probeClaude(runnerOk, {
      which: (_n) => null,
      skillsScan: () => ['should-not-appear'],
    });
    expect(rt.installed).toBe(false);
    expect(rt.skills ?? []).toEqual([]);
  });

  it('技能目录扫子目录名，跳过隐藏与非目录（test_scan_claude_skills_lists_subdirs）', () => {
    const skills = path.join(tmp, 'skills');
    fs.mkdirSync(skills);
    fs.mkdirSync(path.join(skills, 'docx'));
    fs.mkdirSync(path.join(skills, 'pdf'));
    fs.mkdirSync(path.join(skills, '.hidden')); // 隐藏项跳过
    fs.writeFileSync(path.join(skills, 'SCHEMA.md'), 'x'); // 非目录跳过
    expect(scanClaudeSkills(tmp)).toEqual(['docx', 'pdf']);
  });

  it('技能目录缺失退化 []（test_scan_claude_skills_missing_dir）', () => {
    expect(scanClaudeSkills(path.join(tmp, 'nonexistent'))).toEqual([]);
  });

  it('claude 不在 PATH（test_probe_claude_not_on_path）', async () => {
    const [rt, version] = await probeClaude(runnerOk, { which: (_n) => null });
    expect(rt.installed).toBe(false);
    expect(rt.models).toEqual([]);
    expect(version).toBeNull();
  });

  it('--version 非零退出视为未安装（test_probe_claude_bad_exit）', async () => {
    const [rt] = await probeClaude(runnerFail, { which: (_n) => '/usr/bin/claude' });
    expect(rt.installed).toBe(false);
  });

  it('probe_runtimes 两 runtime 定序在列（test_probe_runtimes_list）', async () => {
    // 注入 runner（测试上下文）→ codex 深探跳过真机 spawn；两 runtime 均在列。
    const rts = await probeRuntimes(runnerOk);
    expect(rts).toHaveLength(2);
    expect(rts[0]!.runtime).toBe('claude_code');
    expect(rts[1]!.runtime).toBe('codex');
  });

  it('版本解析（test_parse_version）', () => {
    expect(parseVersion('2.1.205 (Claude Code)')).toBe('2.1.205');
    expect(parseVersion('no version here')).toBeNull();
  });

  it('symlink 不跟随（test_scan_claude_skills_skips_symlinks）', (ctx) => {
    // symlink 不跟随（review #3：防指向大目录/循环）——即便指向真目录也跳过。
    const skills = path.join(tmp, 'skills');
    fs.mkdirSync(skills);
    fs.mkdirSync(path.join(skills, 'real'));
    const target = path.join(tmp, 'elsewhere');
    fs.mkdirSync(target);
    const link = path.join(skills, 'linked');
    try {
      fs.symlinkSync(target, link, 'dir');
    } catch {
      try {
        // win32 无 symlink 特权时回退 junction（libuv 同报 isSymbolicLink，测同一生产分支）。
        fs.symlinkSync(target, link, 'junction');
      } catch {
        ctx.skip(); // ≡ py pytest.skip("symlink 不可用（Windows 无权限/平台限制）")
        return;
      }
    }
    expect(scanClaudeSkills(tmp)).toEqual(['real']);
  });
});
