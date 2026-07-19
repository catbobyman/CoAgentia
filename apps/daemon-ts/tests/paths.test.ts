/** DataPaths 目录布局与会话簿记（对等基准 = py paths.py 行为；契约 D §9.1/§9.3）。 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DataPaths } from '../src/paths.ts';

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-paths-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe('DataPaths', () => {
  it('ensureDirs 建齐五目录', () => {
    const p = new DataPaths(path.join(tmp, 'root'));
    p.ensureDirs();
    for (const d of [p.daemonDir, p.bufferDir, p.stateDir, p.agentsDir, p.worktreesDir]) {
      expect(fs.statSync(d).isDirectory()).toBe(true);
    }
    expect(p.logPath).toBe(path.join(p.daemonDir, 'daemon.log'));
  });

  it('worktreePath = worktrees/<project_id>/<task_id>', () => {
    const p = new DataPaths(tmp);
    expect(p.worktreePath('01PROJ', '01TASK')).toBe(path.join(tmp, 'worktrees', '01PROJ', '01TASK'));
  });

  it('clearAgentHome 清空内容保留目录；不存在则建目录', () => {
    const p = new DataPaths(tmp);
    const home = p.ensureAgentHome('01MEMBER');
    fs.writeFileSync(path.join(home, 'a.txt'), 'x');
    fs.mkdirSync(path.join(home, 'sub'));
    fs.writeFileSync(path.join(home, 'sub', 'b.txt'), 'y');
    p.clearAgentHome('01MEMBER');
    expect(fs.statSync(home).isDirectory()).toBe(true);
    expect(fs.readdirSync(home)).toEqual([]);
    p.clearAgentHome('01ABSENT');
    expect(fs.statSync(p.agentHome('01ABSENT')).isDirectory()).toBe(true);
  });

  it('会话簿记读写清；损坏 JSON 回空对象', () => {
    const p = new DataPaths(tmp);
    expect(p.readSession('01M')).toEqual({});
    p.writeSession('01M', { session_id: 's-中文', n: 1 });
    expect(p.readSession('01M')).toEqual({ session_id: 's-中文', n: 1 });
    fs.writeFileSync(p.sessionFile('01M'), '{broken', 'utf-8');
    expect(p.readSession('01M')).toEqual({});
    p.clearSession('01M');
    expect(fs.existsSync(p.sessionFile('01M'))).toBe(false);
    p.clearSession('01M'); // 幂等
  });
});

// py tests/test_paths.py 对等补齐（TS 迁移批体例 5：py 用例逐条对应核对）：
// - test_ensure_dirs_creates_subtree ↔ 上方「ensureDirs 建齐五目录」；
// - test_worktree_path_uses_project_and_task_ids ↔ 上方「worktreePath = ...」；
// - test_clear_agent_home_keeps_dir_removes_contents ↔ 上方「clearAgentHome 清空内容保留目录...」；
// - test_session_bookkeeping_roundtrip ↔ 上方「会话簿记读写清...」（写→读→清→读空已覆盖）；
// - 其余两例既有文件缺失，在此补齐。
describe('DataPaths（py test_paths.py 缺失用例补齐）', () => {
  // py test_agent_home_uses_member_id
  it('agentHome 用 member_id 命名并建目录', () => {
    const p = new DataPaths(path.join(tmp, 'root'));
    const home = p.ensureAgentHome('01K5AGENT0000000000000000A');
    expect(fs.statSync(home).isDirectory()).toBe(true);
    expect(path.basename(home)).toBe('01K5AGENT0000000000000000A');
  });

  // py test_default_root_is_home_coagentia
  it('默认根目录 = ~/.coagentia', () => {
    const p = new DataPaths();
    expect(p.root).toBe(path.join(os.homedir(), '.coagentia'));
  });
});
