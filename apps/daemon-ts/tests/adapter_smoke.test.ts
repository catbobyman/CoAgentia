/**
 * 真 CLI 冒烟（契约 E §10；锚定 claude 2.1.205 真实帧）。
 *
 * 默认跳过（不烧 token / 不依赖登录）；开启：设 `COAGENTIA_SMOKE=1` 且 claude 在 PATH。
 * 覆盖用例 1（启动就绪）/ 2（一次完整对话 + usage 恰一条 ULID）/ 3（Restart --resume 保上下文）/
 * 7（帧防腐：真契约外帧被计数 + 桩帧不崩）。
 *
 * 结论（写入 open_issues）：
 * - §11.2 --verbose 必需已确认；且 --verbose 灌 stderr → 适配器必须持续排空 stderr（否则死锁）。
 * - §11.3 实测：stream-json 输入模式下 init 帧在**首个 stdin 输入后**才到；就绪 idle 解耦于 init，
 *   会话确认由 result/init 记（router.confirmed）。busy 期继续写 stdin：CLI 按序排队消化。
 * - §11.4 DISALLOWED_TOOLS 初值 EnterPlanMode/ExitPlanMode 生效，未见副作用。
 *
 * 对等基准 = apps/daemon tests/test_adapter_smoke.py（3 用例逐条对应）。
 * py/TS 测试侧差异（登记）：
 * - py pytest.mark.slow + skipif → vitest describe.skipIf（同门：COAGENTIA_SMOKE=1 且 which claude）；
 * - py shutil.which → 手写 PATH×PATHEXT 扫描（win32 语义近似，仅作跳过门）；
 * - MCP roundtrip 的 serve_stdio 面按 TS mcp.ts 实际 API（serveStdio(http, chunks, sink)；
 *   ToolRequest.json_body → jsonBody）绑定，语义与 py io.StringIO 驱动等价。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { AgentBoot } from '@coagentia/contracts-ts';

import type { ClaudeCodeProcess } from '../src/adapters/claude_code.ts';
import { ClaudeCodeAdapter } from '../src/adapters/claude_code.ts';
import * as mcp from '../src/adapters/mcp.ts';
import { DataPaths } from '../src/paths.ts';
import { RecordingSink } from './adapter_helpers.ts';
import { until } from './helpers.ts';

const AID = '01K5CMPT00000000000000000A';
const CHAN = '01K5CHAN00000000000000000A';

/** py shutil.which('claude') 对等（win32 PATHEXT 近似；仅作冒烟跳过门）。 */
function whichClaude(): string | null {
  const exts =
    process.platform === 'win32'
      ? (process.env['PATHEXT'] ?? '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean)
      : [''];
  for (const dir of (process.env['PATH'] ?? '').split(path.delimiter)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, `claude${ext.toLowerCase()}`);
      try {
        if (fs.statSync(candidate).isFile()) return candidate;
      } catch {
        // 不存在：继续
      }
    }
  }
  return null;
}

// 真 CLI 冒烟门：设 COAGENTIA_SMOKE=1 且 claude 已登录（py pytestmark 对等，整文件同门）。
const smokeEnabled = process.env['COAGENTIA_SMOKE'] === '1' && whichClaude() !== null;

describe.skipIf(!smokeEnabled)('真 CLI 冒烟（契约 E §10）', () => {
  let tmp: string;

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-adapter-smoke-'));
  });

  afterEach(() => {
    try {
      fs.rmSync(tmp, { recursive: true, force: true });
    } catch {
      // win32 残留句柄：真进程已由 adapter.stop 杀链收尾，目录清理失败不判失败
    }
  });

  function makeSmoke(): [ClaudeCodeAdapter, RecordingSink, DataPaths, AgentBoot] {
    const paths = new DataPaths(path.join(tmp, 'root'));
    paths.ensureDirs();
    const adapter = new ClaudeCodeAdapter(paths, {
      serverUrl: 'http://127.0.0.1:1',
      apiKey: 'cak_smoke',
    });
    const sink = new RecordingSink();
    adapter.bind(sink);
    const b: AgentBoot = {
      agent_member_id: AID,
      name: 'Pat',
      runtime: 'claude_code',
      model: 'claude-opus-4-8',
      home_path: path.join(tmp, 'home'),
      skills: [],
    };
    return [adapter, sink, paths, b];
  }

  function previews(sink: RecordingSink): string[] {
    return sink.diagnostics
      .filter((d) => d.type === 'agent.turn_output')
      .map((d) => String(((d.payload ?? {}) as Record<string, unknown>)['preview'] ?? ''));
  }

  async function runTurn(
    adapter: ClaudeCodeAdapter,
    sink: RecordingSink,
    body: string,
    timeout = 150_000,
  ): Promise<void> {
    const n = sink.usage.length;
    const msg = {
      id: `01K5MSG1000000000000000${String.fromCharCode(65 + (n % 26))}0`,
      channel_id: CHAN,
      author_member_id: '01K5AUTH00000000000000000A',
      created_at: '2026-07-09T00:00:00.000Z',
      body,
    };
    await adapter.deliver(AID, CHAN, [msg], null);
    await until(() => sink.usage.length > n && sink.statuses().at(-1) === 'idle', timeout);
  }

  it('用例 1/2/7：启动就绪 + 完整对话 usage 恰一条 + 帧防腐（test_smoke_case1_2_7_start_turn_usage_anticorruption）', { timeout: 300_000 }, async () => {
    const [adapter, sink, paths, b] = makeSmoke();
    try {
      // 用例 1：启动就绪 → idle
      expect(await adapter.start(b)).toBe(true);
      await until(() => sink.statuses().includes('idle'), 30_000);

      // 用例 2：一次完整对话 → busy + activity → result → idle + usage 恰一条
      await runTurn(adapter, sink, 'Reply in plain text with exactly: PONG. No tools.');
      expect(sink.statuses()).toContain('busy');
      expect(sink.statuses().at(-1)).toBe('idle');
      expect(sink.usage).toHaveLength(1); // result 帧唯一提取点，ULID 去重
      const ev = sink.usage[0]!;
      expect(ev.id).toHaveLength(26); // 适配器 ULID
      expect(ev.input_tokens).toBeGreaterThan(0);
      expect(ev.output_tokens).toBeGreaterThan(0);
      expect(ev.source_session).toBeTruthy();
      expect(String(ev.source_session)).toContain('-'); // session_id 是 UUID
      expect(paths.readSession(AID)['session_id']).toBe(ev.source_session);
      // activity 相位来自真流（相位切换粒度）
      expect(sink.activity.length, '应有 activity 相位上报').toBeGreaterThan(0);
      expect(sink.activity.every(([, d]) => Boolean(d))).toBe(true);

      // 用例 7：帧防腐——真契约外帧已被计数（无崩溃）
      const router = (adapter.agents.get(AID)!.process as ClaudeCodeProcess).router;
      expect(
        Object.keys(router.unknownCounts).length,
        '真流应出现契约外帧（system/status、rate_limit_event 等）',
      ).toBeGreaterThan(0);
      // 再注入契约外桩帧 → 不崩、计数 +1
      const before = router.unknownCounts['rate_limit_event'] ?? 0;
      await router.process({ type: 'rate_limit_event', rate_limit_info: { status: 'allowed' } });
      await router.process({ type: 'system', subtype: 'notification' });
      expect(router.unknownCounts['rate_limit_event']).toBe(before + 1);
      expect(sink.diagTypes()).toContain('agent.unknown_frame');
    } finally {
      await adapter.stop(AID);
    }
  });

  it('用例 3：Restart --resume 保上下文（test_smoke_case3_restart_resume_keeps_context）', { timeout: 600_000 }, async () => {
    const [adapter, sink, paths, b] = makeSmoke();
    try {
      await adapter.start(b);
      await until(() => sink.statuses().includes('idle'), 30_000);
      // 建立上下文
      await runTurn(adapter, sink, 'Remember codeword BANANA123. No tools; reply plain text: acknowledged.');
      const session1 = paths.readSession(AID)['session_id'];
      expect(session1).toBeTruthy();

      // Restart（一档）→ --resume 保上下文
      await adapter.restart(b);
      await until(() => sink.statuses().includes('idle'), 30_000);
      const resumeArgs = adapter.agents.get(AID)!.process.resetSessionArgs();
      expect(resumeArgs).toEqual(['--resume', session1]); // 精确续接同一会话

      // 追问 → Agent 记得
      const nBefore = previews(sink).length;
      await runTurn(adapter, sink, 'What codeword did I ask you to remember? No tools; reply just the codeword.');
      const recalled = previews(sink).slice(nBefore).join(' ');
      expect(recalled, `Restart 后应记得上下文，实际预览: ${recalled.slice(0, 200)}`).toContain('BANANA123');
    } finally {
      await adapter.stop(AID);
    }
  });

  it('用例扩展：Agent 经真 MCP JSON-RPC 走 list_tasks → claim → set_status（test_smoke_mcp_task_tools_roundtrip）', async () => {
    // StubHttp 集成：因夹具 server_url 为死地址 127.0.0.1:1，用路由桩替真 server 打通 serveStdio 全链路。
    const http = new RoutingHttp();
    const lines = [
      JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }),
      JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }),
      JSON.stringify({
        jsonrpc: '2.0',
        id: 2,
        method: 'tools/call',
        params: { name: 'list_tasks', arguments: { status: 'todo' } },
      }),
      JSON.stringify({
        jsonrpc: '2.0',
        id: 3,
        method: 'tools/call',
        params: { name: 'claim_task', arguments: { task_id: 'T1' } },
      }),
      JSON.stringify({
        jsonrpc: '2.0',
        id: 4,
        method: 'tools/call',
        params: { name: 'set_task_status', arguments: { task_id: 'T1', to: 'in_progress' } },
      }),
    ];
    const sink = {
      data: '',
      write(chunk: string): boolean {
        this.data += chunk;
        return true;
      },
    };
    await mcp.serveStdio(http.http, [Buffer.from(lines.join('\n') + '\n', 'utf-8')], sink);
    const responses = sink.data
      .split('\n')
      .filter((x) => x.trim())
      .map((x) => JSON.parse(x) as { id: number | null; result: { isError: boolean } });
    // initialize + 3 tools/call（notification 无响应）
    expect(responses.map((r) => r.id)).toEqual([1, 2, 3, 4]);
    for (const r of responses.slice(1)) {
      expect(r.result.isError).toBe(false);
    }
    // 请求序列正确落到三个端点
    expect(http.calls[0]!.path).toBe('/api/tasks');
    expect(http.calls[0]!.query).toEqual({ status: 'todo' });
    expect(http.calls[1]!.path).toBe('/api/tasks/T1/claim');
    expect(http.calls[1]!.method).toBe('POST');
    expect(http.calls[2]!.path).toBe('/api/tasks/T1/status');
    expect(http.calls[2]!.jsonBody).toEqual({ to: 'in_progress' });
  });
});

/** 按 (method, path) 路由的桩 HTTP：模拟真 server 对任务工具的响应序列（py _RoutingHttp 对等）。 */
class RoutingHttp {
  calls: mcp.ToolRequest[] = [];

  http = async (req: mcp.ToolRequest): Promise<mcp.ToolResult> => {
    this.calls.push(req);
    if (req.method === 'GET' && req.path === '/api/tasks') {
      const items = [{ id: 'T1', status: 'todo' }];
      return { status: 200, data: { items, next_cursor: null }, isError: false };
    }
    // 真 server claim/status 返回裸 TaskPublic（tasks.py return task_public(...)），
    // 桩形状必须一致——否则冒烟固化一个 server 从不产生的 {"task": ...} 包裹形。
    if (req.path === '/api/tasks/T1/claim') {
      return { status: 200, data: { id: 'T1', status: 'in_progress', owner_member_id: AID }, isError: false };
    }
    if (req.path === '/api/tasks/T1/status') {
      return { status: 200, data: { id: 'T1', status: 'in_progress' }, isError: false };
    }
    return { status: 404, data: { code: 'NOT_FOUND' }, isError: true };
  };
}
