/**
 * 输入编码（E §6）+ 命令行拼装 / 配置隔离（E §2/§3）单测。
 * 对等基准 = apps/daemon tests/test_adapter_encoding_cmdline.py（9 用例逐条对应）。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';

import type { AgentBoot } from '@coagentia/contracts-ts';

import * as cmdline from '../src/adapters/cmdline.ts';
import * as encoding from '../src/adapters/encoding.ts';
import { DISALLOWED_TOOLS } from '../src/generated/constants.ts';

const AID = '01K5CMPT00000000000000000A';

function boot(overrides: Partial<AgentBoot> = {}): AgentBoot {
  return {
    agent_member_id: AID,
    name: 'Pat',
    runtime: 'claude_code',
    model: 'claude-opus-4-8',
    home_path: '/tmp/home/pat',
    skills: ['writing-plans'],
    ...overrides,
  };
}

// tmp 目录（py tmp_path fixture 对等）：逐测创建，afterEach 清理。
const tmpDirs: string[] = [];

function mkTmp(): string {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-cmdline-'));
  tmpDirs.push(d);
  return d;
}

afterEach(() => {
  while (tmpDirs.length > 0) {
    fs.rmSync(tmpDirs.pop()!, { recursive: true, force: true });
  }
});

// ---------------- 输入编码 ----------------

describe('输入编码（E §6）', () => {
  it('render_deliver 模板：批首投递原因 + [#频道] @作者 (时间): 正文', () => {
    // deliver 渲染 = 运行时无关正文（管理器单点，纪律 8）；载体封装归各 Process。
    const msgs = [
      {
        id: '01K5MSG100000000000000000A',
        channel_id: '01K5CHAN00000000000000000A',
        author_member_id: '01K5AUTH00000000000000000A',
        created_at: '2026-07-09T01:02:03.000Z',
        body: '你好世界',
      },
    ];
    const text = encoding.renderDeliver(msgs, {
      reason: 'mention',
      threadRootId: '01K5THRD00000000000000000A',
    });
    expect(text).toContain('[投递'); // 批首投递原因
    expect(text).toContain('有人 @你');
    // 模板 [#频道] @作者 (时间): 正文
    expect(text).toContain('[#01K5CHAN00000000000000000A] @01K5AUTH00000000000000000A ');
    expect(text).toContain('(2026-07-09T01:02:03.000Z): 你好世界');
  });

  it('render_inject 首行系统标注（含来源）', () => {
    const text = encoding.renderInject('修复清单如下', { kind: 'repair', ref: 'err-1' });
    expect(text.startsWith('[system → 仅你可见] (repair: err-1)\n')).toBe(true);
    expect(text).toContain('修复清单如下');
  });

  it('user_frame_line 是单行 JSON（正文含换行也不折行）', () => {
    const line = encoding.userFrameLine('a\nb'); // 正文含换行也必须是单行 JSON
    expect(line).not.toContain('\n');
    const parsed = JSON.parse(line) as {
      message: { content: Array<{ text: string }> };
    };
    expect(parsed.message.content[0]!.text).toBe('a\nb');
  });
});

// ---------------- 命令行 / 隔离 ----------------

describe('命令行 / 隔离（E §2/§3）', () => {
  it('build_argv 核心旗标齐全（stream-json 双向 + --verbose + disallowed-tools 逐个元素）', () => {
    const argv = cmdline.buildArgv(boot(), { mcpConfigPath: '/x/coagentia-mcp.json' });
    const joined = argv.join(' ');
    expect(argv[0]).toBe('claude');
    for (const flag of [
      '--output-format',
      'stream-json',
      '--input-format',
      '--include-partial-messages',
      '--permission-mode',
      'bypassPermissions',
      '--verbose',
      '--mcp-config',
      '--append-system-prompt',
    ]) {
      expect(argv, flag).toContain(flag);
    }
    expect(argv).toContain('--model');
    expect(argv).toContain('claude-opus-4-8');
    // disallowed tools 逐个 argv 元素
    for (const tool of DISALLOWED_TOOLS) {
      expect(argv).toContain(tool);
    }
    expect(argv).not.toContain('--resume'); // 无 resume 参数
    expect(joined).toContain('--strict-mcp-config');
  });

  it('build_argv resume：--resume 后跟会话 id', () => {
    const argv = cmdline.buildArgv(boot(), { resumeSessionId: 'sess-uuid' });
    const i = argv.indexOf('--resume');
    expect(i).toBeGreaterThan(-1);
    expect(argv[i + 1]).toBe('sess-uuid');
  });

  it('身份注入含必备要素（名字/member_id/工具用法/护栏/交付纪律）', () => {
    const text = cmdline.buildIdentityPrompt(boot());
    expect(text).toContain('Pat');
    expect(text).toContain(AID);
    expect(text.toLowerCase()).toContain('coagentia'); // 工具用法
    expect(text.toLowerCase()).toContain('held'); // 护栏约定
    expect(text).toContain('submit_task_contract'); // B5 交付纪律：置 in_review/done 前提交 handoff
    // R2 实测教训（2026-07-19）：交付不 @ 派活人 → 协调者不被唤醒，in_review 停滞。
    expect(text).toContain('交付消息并 @ 派活人');
  });

  it('build_env 隔离 CLAUDE_CONFIG_DIR（不污染其余 env）', () => {
    const env = cmdline.buildEnv('/home/pat', { PATH: '/usr/bin' });
    expect(env['CLAUDE_CONFIG_DIR']).toBe(path.join('/home/pat', '.claude'));
    expect(env['PATH']).toBe('/usr/bin');
  });

  it('materialize_mcp_config 写入 stdio server 定义（含身份/服务端/密钥参数）', () => {
    const tmp = mkTmp();
    const p = cmdline.materializeMcpConfig(path.join(tmp, '.claude'), {
      agentMemberId: AID,
      serverUrl: 'http://s',
      apiKey: 'cak_x',
    });
    const cfg = JSON.parse(fs.readFileSync(p, 'utf-8')) as {
      mcpServers: { coagentia: { type: string; args: string[] } };
    };
    const server = cfg.mcpServers.coagentia;
    expect(server.type).toBe('stdio');
    expect(server.args).toContain('mcp');
    expect(server.args).toContain(AID);
    expect(server.args).toContain('http://s');
    expect(server.args).toContain('cak_x');
  });

  it('materialize_credentials 选最新有效同侪凭证（OAuth 评分逐位比较）', () => {
    const tmp = mkTmp();
    const writeCredentials = (p: string, expiresAt: number): void => {
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(
        p,
        JSON.stringify({
          claudeAiOauth: {
            accessToken: `access-${expiresAt}`,
            refreshToken: `refresh-${expiresAt}`,
            expiresAt,
            refreshTokenExpiresAt: expiresAt + 1000,
          },
        }),
        'utf-8',
      );
    };
    const machine = path.join(tmp, 'machine');
    const target = path.join(tmp, 'agents', 'pat', '.claude');
    const peer = path.join(tmp, 'agents', 'hank', '.claude', '.credentials.json');
    writeCredentials(path.join(machine, '.credentials.json'), 0);
    writeCredentials(path.join(target, '.credentials.json'), 0);
    writeCredentials(peer, 5000);

    expect(cmdline.materializeCredentials(target, machine)).toEqual(['.credentials.json']);
    const copied = JSON.parse(
      fs.readFileSync(path.join(target, '.credentials.json'), 'utf-8'),
    ) as { claudeAiOauth: { expiresAt: number } };
    expect(copied.claudeAiOauth.expiresAt).toBe(5000);
  });
});
