/**
 * coagentia MCP server 单测（E §3）：工具→REST 映射、JSON-RPC、held 透传。
 * 对等基准 = apps/daemon tests/test_adapter_mcp.py（39 用例逐条对应 + 1 条 TS 增补）。
 *
 * py→TS 移植登记（非行为改进）：
 * - HTTP 注入面异步化（HttpFn 返回 Promise）：StubHttp 以箭头属性 `http` 提供（SpawnRecorder 先例），
 *   call_tool 系用例相应 await。
 * - py 对 contracts.rest 模型 model_validate 的形状校验 → TS 以 `import type` + 字面量 fixture
 *   锚定：赋值即编译期字段名/值域校验，运行时 toEqual 锚构造输出（无运行时校验器）。
 * - py monkeypatch urllib.request.urlopen → TS vi.stubGlobal('fetch', …)（makeFetchHttp 每次
 *   调用解析全局 fetch，同 py 全局补丁面）。
 * - py 真子进程用例（test_mcp_subprocess_utf8_roundtrip_without_ioencoding）经
 *   `python -m coagentia_daemon mcp` 拉起；TS 侧 cli.ts 已落地（W4）→ 直接 spawn
 *   `node …/src/cli.ts mcp --agent-member … --server-url … --api-key …`——与 cmdline.mcpCommand
 *   （即 claude --mcp-config 真实拉起链路）同一入口同构，内部走同一 mcp.run()（W4 收尾清账，
 *   原 wrapper 直调 run() 形态与 it.todo 挂账销账）。
 */

import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AsTask, ContractCreate, LoopContractBody, MessageCreate, ReminderCreate, TaskHandoffBody, TaskMergeAccepted, TaskPlanBody, TaskStatusChange } from '@coagentia/contracts-ts';

import * as cmdline from '../src/adapters/cmdline.ts';
import * as codexCmdline from '../src/adapters/codex_cmdline.ts';
import * as mcp from '../src/adapters/mcp.ts';
import { withTimeout } from '../src/aio.ts';
import { killProcessTree } from '../src/checks.ts';
import { COAGENTIA_MCP_TOOLS } from '../src/generated/constants.ts';

/** 注入 HTTP：记录请求、返回预置响应（py StubHttp 对等；异步化登记见文件头）。 */
class StubHttp {
  status: number;
  data: unknown;
  calls: mcp.ToolRequest[] = [];

  constructor(status = 201, data: unknown = null) {
    this.status = status;
    this.data = data !== null ? data : { ok: true };
  }

  http: mcp.HttpFn = async (req: mcp.ToolRequest): Promise<mcp.ToolResult> => {
    this.calls.push(req);
    return { status: this.status, data: this.data, isError: this.status >= 400 };
  };
}

// call_tool 外层 payload（status/data/held/error 透传形状）；data 逐测各样，
// 宽松 any 索引 = py 动态下标访问对等（本包无 eslint，tsc 允许显式 any）。
/* eslint-disable @typescript-eslint/no-explicit-any */
function parsePayload(out: mcp.CallToolResult): any {
  return JSON.parse(out.content[0]!.text);
}

async function toolsListed(state: mcp._RpcState, id = 1): Promise<mcp.ToolDef[]> {
  const listed = (await mcp.handleRpc({ jsonrpc: '2.0', id, method: 'tools/list' }, state)) as {
    result: { tools: mcp.ToolDef[] };
  };
  return listed.result.tools;
}

// tmp 目录（py tmp_path fixture 对等）：逐测创建，afterEach 清理。
const tmpDirs: string[] = [];

function mkTmp(): string {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-mcp-'));
  tmpDirs.push(d);
  return d;
}

afterEach(() => {
  vi.unstubAllGlobals();
  while (tmpDirs.length > 0) {
    fs.rmSync(tmpDirs.pop()!, { recursive: true, force: true });
  }
});

// ---------------- 工具 → REST 映射 ----------------

describe('build_request 映射（E §3）', () => {
  it('基础映射：send_message/get_thread/cancel_reminder/list_channels/get_messages（test_build_request_mapping）', () => {
    const r = mcp.buildRequest('send_message', {
      channel_id: 'C1',
      body: 'hi',
      thread_root_id: 'T1',
    });
    expect(r.method).toBe('POST');
    expect(r.path).toBe('/api/channels/C1/messages');
    expect(r.jsonBody).toEqual({ body: 'hi', thread_root_id: 'T1' });

    expect(mcp.buildRequest('get_thread', { message_id: 'M9' }).path).toBe(
      '/api/messages/M9/thread',
    );
    expect(mcp.buildRequest('cancel_reminder', { reminder_id: 'R1' }).method).toBe('DELETE');
    expect(mcp.buildRequest('list_channels', {}).path).toBe('/api/channels');
    const gm = mcp.buildRequest('get_messages', { channel_id: 'C1', limit: 5 });
    expect(gm.query).toEqual({ limit: 5 });
  });

  it('M2 六工具 → REST 端点映射（test_build_request_m2_task_tools）', () => {
    // 纯透传，method/path/query/jsonBody 一一断言。
    const lt = mcp.buildRequest('list_tasks', {
      channel_id: 'C1',
      status: 'todo',
      owner: 'M1',
      limit: 5,
    });
    expect(lt.method).toBe('GET');
    expect(lt.path).toBe('/api/tasks');
    expect(lt.query).toEqual({ channel_id: 'C1', status: 'todo', owner: 'M1', limit: 5 });
    expect(lt.jsonBody).toBeNull();

    // 全空过滤 → query 收敛为 null（不发空 querystring）
    expect(mcp.buildRequest('list_tasks', {}).query).toBeNull();

    const gt = mcp.buildRequest('get_task', { task_id: 'T1' });
    expect(gt.method).toBe('GET');
    expect(gt.path).toBe('/api/tasks/T1');

    const cl = mcp.buildRequest('claim_task', { task_id: 'T1' });
    expect(cl.method).toBe('POST');
    expect(cl.path).toBe('/api/tasks/T1/claim');
    expect(cl.jsonBody).toBeNull();

    const un = mcp.buildRequest('unclaim_task', { task_id: 'T1' });
    expect(un.method).toBe('POST');
    expect(un.path).toBe('/api/tasks/T1/unclaim');
    expect(un.jsonBody).toBeNull();

    const ss = mcp.buildRequest('set_task_status', { task_id: 'T1', to: 'in_progress' });
    expect(ss.method).toBe('POST');
    expect(ss.path).toBe('/api/tasks/T1/status');
    expect(ss.jsonBody).toEqual({ to: 'in_progress' });

    const se = mcp.buildRequest('search', {
      q: 'hello',
      kind: 'message',
      from_member: 'M1',
      in_channel: 'C1',
    });
    expect(se.method).toBe('GET');
    expect(se.path).toBe('/api/search');
    expect(se.query).toEqual({ q: 'hello', kind: 'message', from_member: 'M1', in_channel: 'C1' });
  });

  it('set_task_status 的 body 过 TaskStatusChange 契约（test_set_task_status_body_matches_contract）', () => {
    // py model_validate → TS import type + 字面量锚：赋值即编译期字段名/值域校验（登记差异）。
    const req = mcp.buildRequest('set_task_status', { task_id: 'T1', to: 'done' });
    const anchored: TaskStatusChange = { to: 'done' };
    expect(req.jsonBody).toEqual(anchored);
  });

  it('send_message 的 as_task 原样透传且过 MessageCreate 契约（test_send_message_as_task_passthrough）', () => {
    const req = mcp.buildRequest('send_message', {
      channel_id: 'C1',
      body: '做这个',
      as_task: { title: '标题' },
    });
    expect(req.jsonBody!['as_task']).toEqual({ title: '标题' });
    const anchored: MessageCreate = { body: '做这个', as_task: { title: '标题' } };
    expect(req.jsonBody).toEqual(anchored);

    // 空 {} 也透传（server 用缺省 title）
    const empty = mcp.buildRequest('send_message', { channel_id: 'C1', body: 'x', as_task: {} });
    expect(empty.jsonBody!['as_task']).toEqual({});
    const emptyAnchored: MessageCreate = { body: 'x', as_task: {} };
    expect(empty.jsonBody).toEqual(emptyAnchored);

    // 未给 as_task → body 不含该键
    const none = mcp.buildRequest('send_message', { channel_id: 'C1', body: 'x' });
    expect(none.jsonBody).not.toHaveProperty('as_task');
  });

  it('create_reminder 构造的 body 过 ReminderCreate 契约（test_create_reminder_body_matches_contract）', () => {
    // 回归 #1：曾字段全错→422（py model_validate → TS 字面量锚，登记差异）。
    const ulid = '01K5CHAN00000000000000000A';
    // once：时刻写入 cadence，最小必填集
    const once = mcp.buildRequest('create_reminder', {
      kind: 'once',
      cadence: '2026-07-10T09:00:00Z',
      anchor_channel_id: ulid,
    });
    expect(once.path).toBe('/api/reminders');
    expect(once.method).toBe('POST');
    const onceAnchor: ReminderCreate = {
      kind: 'once',
      cadence: '2026-07-10T09:00:00Z',
      anchor_channel_id: ulid,
    };
    expect(once.jsonBody).toEqual(onceAnchor);

    // recurring + 全部可选锚点 + 内联 loop_contract（M4：cadence = interval）
    const loopContract: LoopContractBody = {
      version: 'coagentia.loop-contract.v1',
      cadence: 'PT1H',
      verification: ['每次输出附校验命令'],
      budget: { max_retries: 1, max_runtime_min: 10 },
      tools: [],
      escalation: '连续两次失败拉创建者',
    };
    const rec = mcp.buildRequest('create_reminder', {
      kind: 'recurring',
      cadence: 'PT1H',
      anchor_channel_id: ulid,
      anchor_message_id: '01K5MSG100000000000000000A',
      anchor_task_id: '01K5TASK00000000000000000A',
      loop_contract: loopContract,
    });
    expect(rec.jsonBody!['loop_contract']).toEqual(loopContract); // 对象原样透传
    const recAnchor: ReminderCreate = {
      kind: 'recurring',
      cadence: 'PT1H',
      anchor_channel_id: ulid,
      anchor_message_id: '01K5MSG100000000000000000A',
      anchor_task_id: '01K5TASK00000000000000000A',
      loop_contract: loopContract,
    };
    expect(rec.jsonBody).toEqual(recAnchor);
  });

  it('TOOLS 名集 = 契约目录（19 无遗漏/多发明）（test_tool_catalog_matches_contract）', () => {
    // constants.COAGENTIA_MCP_TOOLS 已收编 create_task/trigger_merge；py 并集写法两态均成立，
    // 此处照抄（收编态下自然退化为纯等值），19 计数守门同步保留。
    const names = mcp.TOOLS.map((t) => t.name);
    expect(new Set(names)).toEqual(new Set([...COAGENTIA_MCP_TOOLS, 'create_task', 'trigger_merge']));
    expect(names.length).toBe(new Set(names).size); // 无重复
    expect(names.length).toBe(19); // 契约 E v1.7 工具总数
    expect(names).toContain('trigger_deploy');
  });
});

// ---------------- call_tool 透传 ----------------

describe('call_tool 透传（E §3）', () => {
  it('claim_task 收 409 CLAIM_RACE → isError 且 data 原样带 code/details（test_claim_task_race_passthrough）', async () => {
    const data = { code: 'CLAIM_RACE', details: { current_owner: '01K5MEMB00000000000000000A' } };
    const stub = new StubHttp(409, data);
    const out = await mcp.callTool('claim_task', { task_id: 'T1' }, stub.http);
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(409);
    expect(payload.data.code).toBe('CLAIM_RACE');
    expect(payload.data.details.current_owner).toBe('01K5MEMB00000000000000000A');
  });

  it('set_task_status 收 422 TASK_TRANSITION_INVALID → isError 原样透传（test_set_task_status_transition_invalid_passthrough）', async () => {
    const stub = new StubHttp(422, { code: 'TASK_TRANSITION_INVALID' });
    const out = await mcp.callTool('set_task_status', { task_id: 'T1', to: 'done' }, stub.http);
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(422);
    expect(payload.data.code).toBe('TASK_TRANSITION_INVALID');
  });

  it('成功：201 status/data 原样（test_call_tool_success）', async () => {
    const stub = new StubHttp(201, { message: { id: '01K5MSG100000000000000000A' } });
    const out = await mcp.callTool('send_message', { channel_id: 'C1', body: 'hi' }, stub.http);
    expect(out.isError).toBe(false);
    const payload = parsePayload(out);
    expect(payload.status).toBe(201);
    expect(payload.data.message.id).toBe('01K5MSG100000000000000000A');
  });

  it('freshness 命中 → 202 held 原样结构化透传（test_call_tool_held_passthrough）', async () => {
    // M4 前不触发，形状先对。
    const data = { held_draft: { id: '01K5HELD0000000000000000A' }, reasons: ['stale'] };
    const stub = new StubHttp(202, data);
    const out = await mcp.callTool('send_message', { channel_id: 'C1', body: 'hi' }, stub.http);
    const payload = parsePayload(out);
    expect(payload.status).toBe(202);
    expect(payload.held).toBe(true);
    expect(payload.data.reasons).toEqual(['stale']);
  });

  it('未知工具不触 HTTP（test_call_tool_unknown_tool）', async () => {
    const stub = new StubHttp();
    const out = await mcp.callTool('nonexistent_tool', {}, stub.http);
    expect(out.isError).toBe(true);
    expect(stub.calls).toEqual([]);
  });

  it('4xx 状态 → isError（test_call_tool_error_status）', async () => {
    const stub = new StubHttp(422, { code: 'LOOP_CONTRACT_REQUIRED' });
    const out = await mcp.callTool(
      'create_reminder',
      { kind: 'recurring', cadence: '0 9 * * *', anchor_channel_id: '01K5CHAN00000000000000000A' },
      stub.http,
    );
    expect(out.isError).toBe(true);
    expect(parsePayload(out).status).toBe(422);
  });
});

// ---------------- M7 trigger_deploy ----------------

describe('trigger_deploy（契约 E v1.5，R8）', () => {
  it('→ POST /api/projects/{id}/deployments，空请求体（test_build_request_trigger_deploy）', () => {
    const r = mcp.buildRequest('trigger_deploy', { project_id: 'P1' });
    expect(r.method).toBe('POST');
    expect(r.path).toBe('/api/projects/P1/deployments');
    expect(r.jsonBody).toBeNull();
    expect(r.query).toBeNull();
  });

  it('409 DEPLOY_IN_PROGRESS → isError 且 status/data 原样透传（test_trigger_deploy_in_progress_passthrough）', async () => {
    const stub = new StubHttp(409, { code: 'DEPLOY_IN_PROGRESS' });
    const out = await mcp.callTool('trigger_deploy', { project_id: 'P1' }, stub.http);
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(409);
    expect(payload.data.code).toBe('DEPLOY_IN_PROGRESS');
    // 请求确实发到 deployments 端点
    expect(stub.calls[0]!.path).toBe('/api/projects/P1/deployments');
    expect(stub.calls[0]!.method).toBe('POST');
  });

  it('422 VALIDATION_FAILED（无 deploy_command）→ isError 原样透传（test_trigger_deploy_validation_failed_passthrough）', async () => {
    const data = { code: 'VALIDATION_FAILED', details: { hint: '先配置 deploy_command' } };
    const stub = new StubHttp(422, data);
    const out = await mcp.callTool('trigger_deploy', { project_id: 'P1' }, stub.http);
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(422);
    expect(payload.data.code).toBe('VALIDATION_FAILED');
    expect(payload.data.details.hint).toBe('先配置 deploy_command');
  });

  it('503 DAEMON_OFFLINE → isError 原样透传（test_trigger_deploy_daemon_offline_passthrough）', async () => {
    const stub = new StubHttp(503, { code: 'DAEMON_OFFLINE' });
    const out = await mcp.callTool('trigger_deploy', { project_id: 'P1' }, stub.http);
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(503);
    expect(payload.data.code).toBe('DAEMON_OFFLINE');
  });

  it('R8 留痕真调用链：出站请求注入 Bearer + X-Acting-Member（test_trigger_deploy_injects_acting_member_header）', async () => {
    // py monkeypatch urllib.request.urlopen → TS vi.stubGlobal('fetch')（登记差异）。
    const captured: { url?: string; method?: string; headers?: Record<string, string> } = {};
    const fakeFetch = async (
      input: string | URL,
      init?: { method?: string; headers?: Record<string, string> },
    ): Promise<Response> => {
      captured.url = String(input);
      captured.method = init?.method;
      captured.headers = { ...init?.headers };
      return new Response('{"id":"D1","status":"queued"}', {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      });
    };
    vi.stubGlobal('fetch', fakeFetch);

    const http = mcp.makeFetchHttp('http://srv', 'secret-key', '01AGENTMEMBER0000000000000');
    const out = await mcp.callTool('trigger_deploy', { project_id: 'P1' }, http);

    expect(out.isError).toBe(false);
    expect(captured.method).toBe('POST');
    expect(captured.url).toBe('http://srv/api/projects/P1/deployments');
    const headers = Object.fromEntries(
      Object.entries(captured.headers!).map(([k, v]) => [k.toLowerCase(), v]),
    );
    expect(headers['authorization']).toBe('Bearer secret-key');
    expect(headers['x-acting-member']).toBe('01AGENTMEMBER0000000000000');
  });

  it('tools/list 往返：trigger_deploy 出现且 project_id 必填（test_tools_list_includes_trigger_deploy）', async () => {
    const state = new mcp._RpcState(new StubHttp().http);
    const tools = await toolsListed(state);
    const td = tools.find((t) => t.name === 'trigger_deploy')!;
    expect(td.inputSchema.required).toEqual(['project_id']);
  });
});

// ---------------- M8-B5 submit_task_contract ----------------

/**
 * 最小合法 TaskHandoffBody（置 in_review 门要 deliverables≥1；此处即满足）。
 *
 * member_id 用合法 ULID（Crockford base32，无 I/L/O/U）；返回类型 = 契约 TaskHandoffBody
 * （py Ulid 模式校验 → TS 编译期字段/值域锚，登记差异）。
 */
function handoffBody(): TaskHandoffBody {
  return {
    version: 'coagentia.task-handoff.v1',
    from_member: '01AGENTMEMBER0000000000000',
    to_member: '01K5MEMB00000000000000000A',
    deliverables: [{ path: '/repo/x.py', kind: 'file' }],
    evidence: [{ type: 'command', ref: 'pytest -q → 48 passed', conclusion: '全绿' }],
    verify_plan: '复跑 pytest -q 复核',
  };
}

describe('submit_task_contract（契约 E v1.6）', () => {
  it('→ POST /api/tasks/{id}/contracts，{kind, body} 原样透传（两 kind）（test_build_request_submit_task_contract）', () => {
    const body = handoffBody();
    const r = mcp.buildRequest('submit_task_contract', {
      task_id: 'T1',
      kind: 'task_handoff',
      body,
    });
    expect(r.method).toBe('POST');
    expect(r.path).toBe('/api/tasks/T1/contracts');
    expect(r.jsonBody).toEqual({ kind: 'task_handoff', body });
    expect(r.query).toBeNull();

    const planBody: TaskPlanBody = {
      version: 'coagentia.task-plan.v1',
      goal: '做个东西',
      acceptance_criteria: [
        { id: 'ac1', statement: '命令退 0', verify_by: 'command', verify_ref: 'make test' },
      ],
    };
    const p = mcp.buildRequest('submit_task_contract', {
      task_id: 'T2',
      kind: 'task_plan',
      body: planBody,
    });
    expect(p.path).toBe('/api/tasks/T2/contracts');
    expect(p.jsonBody).toEqual({ kind: 'task_plan', body: planBody });
  });

  it('构造的 {kind, body} 过 ContractCreate；body 过 kind 模型（test_submit_task_contract_body_matches_contract）', () => {
    const req = mcp.buildRequest('submit_task_contract', {
      task_id: 'T1',
      kind: 'task_handoff',
      body: handoffBody(),
    });
    const anchoredCreate: ContractCreate = { kind: 'task_handoff', body: handoffBody() };
    expect(req.jsonBody).toEqual(anchoredCreate); // {kind, body} 形状对齐 POST 端点
    const anchoredBody: TaskHandoffBody = handoffBody();
    expect(req.jsonBody!['body']).toEqual(anchoredBody); // body 过 kind 模型 = 首投即可通过 T7
    // py 尾断言 CONTRACT_BODY_MODELS[ContractKind.TASK_HANDOFF] is TaskHandoffBody 是契约包
    // 运行时映射内部不变量——TS 无运行时模型注册表，kind↔body 对应由上两类型锚共同表达（登记差异）。
  });

  it('必填 body 缺失 → 不触 HTTP，收敛为 missing_argument（test_submit_task_contract_missing_body_arg）', async () => {
    const stub = new StubHttp();
    const out = await mcp.callTool(
      'submit_task_contract',
      { task_id: 'T1', kind: 'task_plan' },
      stub.http,
    );
    expect(out.isError).toBe(true);
    expect(stub.calls).toEqual([]);
    expect(parsePayload(out).error).toBe('missing_argument');
  });

  it('字段不符 → 422 VALIDATION_FAILED 携逐字段 errors 原样透传（test_submit_task_contract_validation_failed_passthrough）', async () => {
    // Agent 据此按清单修复自愈。
    const data = {
      code: 'VALIDATION_FAILED',
      details: {
        kind: 'task_handoff',
        errors: [{ loc: ['verify_plan'], msg: 'Field required', type: 'missing' }],
      },
    };
    const stub = new StubHttp(422, data);
    const out = await mcp.callTool(
      'submit_task_contract',
      { task_id: 'T1', kind: 'task_handoff', body: { version: 'coagentia.task-handoff.v1' } },
      stub.http,
    );
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(422);
    expect(payload.data.code).toBe('VALIDATION_FAILED');
    expect(payload.data.details.errors[0].loc).toEqual(['verify_plan']);
    expect(stub.calls[0]!.path).toBe('/api/tasks/T1/contracts');
    expect(stub.calls[0]!.method).toBe('POST');
  });

  it('201 创建 → isError=False，status/data（含 revision）原样透传（test_submit_task_contract_success_passthrough）', async () => {
    const stub = new StubHttp(201, {
      id: '01K5CONTRACT0000000000000A',
      kind: 'task_handoff',
      revision: 1,
    });
    const out = await mcp.callTool(
      'submit_task_contract',
      { task_id: 'T1', kind: 'task_handoff', body: handoffBody() },
      stub.http,
    );
    expect(out.isError).toBe(false);
    const payload = parsePayload(out);
    expect(payload.status).toBe(201);
    expect(payload.data.revision).toBe(1);
  });

  it('tools/list 往返：required=task_id/kind/body，kind 枚举恰两值（test_tools_list_includes_submit_task_contract）', async () => {
    const state = new mcp._RpcState(new StubHttp().http);
    const tools = await toolsListed(state);
    const tool = tools.find((t) => t.name === 'submit_task_contract')!;
    expect(new Set(tool.inputSchema.required)).toEqual(new Set(['task_id', 'kind', 'body']));
    const kindEnum = tool.inputSchema.properties!['kind']!['enum'] as string[];
    expect(new Set(kindEnum)).toEqual(new Set(['task_plan', 'task_handoff']));
  });
});

// ---------------- DEDAG（契约 E v1.7）：create_task 委派 / trigger_merge 任务级合并

describe('create_task / trigger_merge（契约 E v1.7）', () => {
  it('create_task → POST /api/channels/{id}/messages，text→body + as_task 三字段装配（test_build_request_create_task）', () => {
    const r = mcp.buildRequest('create_task', {
      channel_id: 'C1',
      text: '@小码 做登录页',
      title: '登录页',
      project_id: '01K5PRJX00000000000000000A',
      writes_code: true,
    });
    expect(r.method).toBe('POST');
    expect(r.path).toBe('/api/channels/C1/messages');
    expect(r.jsonBody).toEqual({
      body: '@小码 做登录页',
      as_task: {
        title: '登录页',
        project_id: '01K5PRJX00000000000000000A',
        writes_code: true,
      },
    });
    expect(r.query).toBeNull();

    // 最小集：仅 channel_id+text → as_task 空 {}（server 缺省 title；writes_code 默认 false）
    const minimal = mcp.buildRequest('create_task', { channel_id: 'C1', text: '先讨论' });
    expect(minimal.jsonBody).toEqual({ body: '先讨论', as_task: {} });
  });

  it('构造 body 过 MessageCreate/AsTask 契约（含 DEDAG 扩展 project_id/writes_code）（test_create_task_body_matches_contract）', () => {
    const req = mcp.buildRequest('create_task', {
      channel_id: 'C1',
      text: '@小码 修复登录',
      title: '修复登录',
      project_id: '01K5PRJX00000000000000000A',
      writes_code: true,
    });
    const anchored: MessageCreate = {
      body: '@小码 修复登录',
      as_task: { title: '修复登录', project_id: '01K5PRJX00000000000000000A', writes_code: true },
    };
    expect(req.jsonBody).toEqual(anchored);
    // py isinstance(parsed.as_task, AsTask) + writes_code is True → TS 编译期 AsTask 字面量锚。
    const asTaskAnchor: AsTask = {
      title: '修复登录',
      project_id: '01K5PRJX00000000000000000A',
      writes_code: true,
    };
    expect(req.jsonBody!['as_task']).toEqual(asTaskAnchor);
    // 空 as_task {} 也过契约（同 send_message as_task={} 先例）；py 断言 pydantic 默认
    // writes_code=False 属运行时默认应用——TS 无运行时校验器，{} 过全可选 AsTask 类型即锚形状，
    // 缺省值语义由 server/py 契约兑现（登记差异）。
    const emptyAsTask: AsTask = {};
    const minimal = mcp.buildRequest('create_task', { channel_id: 'C1', text: 'x' });
    expect(minimal.jsonBody).toEqual({ body: 'x', as_task: emptyAsTask });
  });

  it('必填 text 缺失 → 不触 HTTP，收敛为 missing_argument（test_create_task_missing_text_arg）', async () => {
    const stub = new StubHttp();
    const out = await mcp.callTool('create_task', { channel_id: 'C1' }, stub.http);
    expect(out.isError).toBe(true);
    expect(stub.calls).toEqual([]);
    expect(parsePayload(out).error).toBe('missing_argument');
  });

  it('writes_code=true 缺 project_id → 422 原样透传（test_create_task_writes_code_without_project_422_passthrough）', async () => {
    const data = { code: 'VALIDATION_FAILED', details: { hint: 'writes_code 须携本频道绑定项目' } };
    const stub = new StubHttp(422, data);
    const out = await mcp.callTool(
      'create_task',
      { channel_id: 'C1', text: '@小码 写代码', writes_code: true },
      stub.http,
    );
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(422);
    expect(payload.data.code).toBe('VALIDATION_FAILED');
    expect(stub.calls[0]!.path).toBe('/api/channels/C1/messages');
    expect(stub.calls[0]!.jsonBody!['as_task']).toEqual({ writes_code: true });
  });

  it('201 → MessageCreated（message+task 原子）原样透传（test_create_task_success_passthrough）', async () => {
    // Agent 从 data.task.id 取 task_id。
    const data = {
      message: { id: '01K5MSG100000000000000000A' },
      task: { id: '01K5TASK00000000000000000A', number: 42 },
    };
    const stub = new StubHttp(201, data);
    const out = await mcp.callTool(
      'create_task',
      { channel_id: 'C1', text: '@小码 做登录页', title: '登录页' },
      stub.http,
    );
    expect(out.isError).toBe(false);
    const payload = parsePayload(out);
    expect(payload.status).toBe(201);
    expect(payload.data.task.id).toBe('01K5TASK00000000000000000A');
  });

  it('create_task 与 send_message 同端点同 freshness 护栏：202 → held=True（test_create_task_held_202_marks_held）', async () => {
    const stub = new StubHttp(202, { held_draft: { id: '01K5HELD0000000000000000A' } });
    const out = await mcp.callTool('create_task', { channel_id: 'C1', text: 'hi' }, stub.http);
    const payload = parsePayload(out);
    expect(payload.status).toBe(202);
    expect(payload.held).toBe(true);
  });

  it('trigger_merge → POST /api/tasks/{id}/merge，空请求体（test_build_request_trigger_merge）', () => {
    const r = mcp.buildRequest('trigger_merge', { task_id: 'T1' });
    expect(r.method).toBe('POST');
    expect(r.path).toBe('/api/tasks/T1/merge');
    expect(r.jsonBody).toBeNull();
    expect(r.query).toBeNull();
  });

  it('202 受理回执 → isError=False 且**不标 held**（test_trigger_merge_accepted_202_not_marked_held）', async () => {
    // held 语义专属消息发送面 freshness，误标会让 Agent 误入「停止重发等待直投」姿态。
    // py TaskMergeAccepted.model_validate(data) → TS 类型锚字面量（桩响应对齐契约 202 形状）。
    const data: TaskMergeAccepted = { task_id: '01K5TASK00000000000000000A', status: 'accepted' };
    const stub = new StubHttp(202, data);
    const out = await mcp.callTool(
      'trigger_merge',
      { task_id: '01K5TASK00000000000000000A' },
      stub.http,
    );
    expect(out.isError).toBe(false);
    const payload = parsePayload(out);
    expect(payload.status).toBe(202);
    expect(payload.data.status).toBe('accepted');
    expect(payload).not.toHaveProperty('held');
  });

  it('早已合并 → 202 status=merged 幂等命中原样透传（test_trigger_merge_idempotent_merged_202）', async () => {
    const data: TaskMergeAccepted = { task_id: '01K5TASK00000000000000000A', status: 'merged' };
    const stub = new StubHttp(202, data);
    const out = await mcp.callTool(
      'trigger_merge',
      { task_id: '01K5TASK00000000000000000A' },
      stub.http,
    );
    expect(out.isError).toBe(false);
    expect(parsePayload(out).data.status).toBe('merged');
  });

  it('同项目已有合并在跑 → 409 DEPLOY_IN_PROGRESS 原样透传（test_trigger_merge_in_progress_passthrough）', async () => {
    const stub = new StubHttp(409, { code: 'DEPLOY_IN_PROGRESS' });
    const out = await mcp.callTool('trigger_merge', { task_id: 'T1' }, stub.http);
    expect(out.isError).toBe(true);
    const payload = parsePayload(out);
    expect(payload.status).toBe(409);
    expect(payload.data.code).toBe('DEPLOY_IN_PROGRESS');
    expect(stub.calls[0]!.path).toBe('/api/tasks/T1/merge');
    expect(stub.calls[0]!.method).toBe('POST');
  });

  it('daemon 离线 → 503 DAEMON_OFFLINE 原样透传（test_trigger_merge_daemon_offline_passthrough）', async () => {
    const stub = new StubHttp(503, { code: 'DAEMON_OFFLINE' });
    const out = await mcp.callTool('trigger_merge', { task_id: 'T1' }, stub.http);
    expect(out.isError).toBe(true);
    expect(parsePayload(out).data.code).toBe('DAEMON_OFFLINE');
  });

  it('tools/list 往返：DEDAG 两工具属性面/必填面精确（test_tools_list_includes_dedag_tools）', async () => {
    // create_task 必填恰 channel_id/text 且属性面**无** suggested_owner_member_id
    // （契约 E 文档偏差不实现——建议 owner 走正文 @名字 mention 唤醒）；trigger_merge 必填恰 task_id。
    const state = new mcp._RpcState(new StubHttp().http);
    const tools = await toolsListed(state);
    const byName = new Map(tools.map((t) => [t.name, t]));
    const ct = byName.get('create_task')!;
    expect(new Set(ct.inputSchema.required)).toEqual(new Set(['channel_id', 'text']));
    expect(new Set(Object.keys(ct.inputSchema.properties!))).toEqual(
      new Set(['channel_id', 'text', 'title', 'project_id', 'writes_code']),
    );
    const tm = byName.get('trigger_merge')!;
    expect(tm.inputSchema.required).toEqual(['task_id']);
    expect(Object.keys(tm.inputSchema.properties!)).toEqual(['task_id']);
  });

  it('E2 Codex 零改动：config.toml 拉起同一 mcp 子命令 server（test_codex_reuses_same_mcp_catalog）', () => {
    // 工具目录 runtime 无关：trigger_deploy 经同一 TOOLS 目录对 codex 亦生效，无需 codex 侧改动。
    const [cmd, baseArgs] = cmdline.mcpCommand();
    const toml = codexCmdline.buildConfigToml({
      agentMemberId: 'M1',
      serverUrl: 'http://x',
      apiKey: 'k',
    });
    expect(toml).toContain(JSON.stringify(cmd)); // 同一 daemon mcp 入口 → 同一 mcp.TOOLS 目录
    expect(baseArgs).toContain('mcp');
    // 目录 = 单一事实源，两 runtime 共用；trigger_deploy 无需 codex 侧登记即可用
    expect(mcp.TOOLS.map((t) => t.name)).toContain('trigger_deploy');
  });
});

// ---------------- JSON-RPC / stdio ----------------

describe('JSON-RPC / stdio 循环', () => {
  it('tools/list 往返：M2 六工具全出现且 send_message 声明 as_task（test_tools_list_includes_m2_tools）', async () => {
    const state = new mcp._RpcState(new StubHttp().http);
    const tools = await toolsListed(state);
    const names = new Set(tools.map((t) => t.name));
    for (const n of ['list_tasks', 'get_task', 'claim_task', 'unclaim_task', 'set_task_status', 'search']) {
      expect(names.has(n), n).toBe(true);
    }
    const send = tools.find((t) => t.name === 'send_message')!;
    expect('as_task' in send.inputSchema.properties!).toBe(true);
  });

  it('initialize 与 tools/list（test_jsonrpc_initialize_and_tools_list）', async () => {
    const state = new mcp._RpcState(new StubHttp().http);
    const init = (await mcp.handleRpc(
      { jsonrpc: '2.0', id: 1, method: 'initialize', params: {} },
      state,
    )) as { result: { serverInfo: { name: string }; capabilities: Record<string, unknown> } };
    expect(init.result.serverInfo.name).toBe('coagentia');
    expect('tools' in init.result.capabilities).toBe(true);
    const tools = await toolsListed(state, 2);
    const names = new Set(tools.map((t) => t.name));
    for (const n of ['send_message', 'get_messages', 'list_channels', 'upload_file']) {
      expect(names.has(n), n).toBe(true);
    }
  });

  it('notification 返回 null 且置 initialized（test_jsonrpc_notification_returns_none）', async () => {
    const state = new mcp._RpcState(new StubHttp().http);
    expect(
      await mcp.handleRpc({ jsonrpc: '2.0', method: 'notifications/initialized' }, state),
    ).toBeNull();
    expect(state.initialized).toBe(true);
  });

  it('serve_stdio 全链 roundtrip（test_serve_stdio_full_roundtrip）', async () => {
    const stub = new StubHttp(201, { message: { id: '01K5MSG100000000000000000A' } });
    const lines = [
      JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }),
      JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }),
      JSON.stringify({
        jsonrpc: '2.0',
        id: 2,
        method: 'tools/call',
        params: { name: 'send_message', arguments: { channel_id: 'C1', body: 'hi' } },
      }),
    ];
    const sink = { data: '', write(chunk: string): boolean { this.data += chunk; return true; } };
    await mcp.serveStdio(stub.http, [Buffer.from(lines.join('\n') + '\n', 'utf-8')], sink);
    const responses = sink.data
      .split('\n')
      .filter((x) => x.trim())
      .map((x) => JSON.parse(x) as { id: number | null; result?: { isError?: boolean } });
    // initialize + tools/call 两条响应（notification 无响应）
    expect(responses).toHaveLength(2);
    expect(responses[0]!.id).toBe(1);
    expect(responses[1]!.id).toBe(2);
    expect(responses[1]!.result!.isError).toBe(false);
    expect(stub.calls).toHaveLength(1);
  });

  it('不可解析行必须回 parse error（id=null）不静默丢弃（test_serve_stdio_parse_error_replies_not_silent）', async () => {
    // CR-M8-2：修复前静默丢弃 → claude 侧对应 tools/call 无限等待（win32 GBK 吞结构引号即触发此路径）。
    const sink = { data: '', write(chunk: string): boolean { this.data += chunk; return true; } };
    await mcp.serveStdio(
      new StubHttp().http,
      [Buffer.from('{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params\n', 'utf-8')],
      sink,
    );
    const responses = sink.data
      .split('\n')
      .filter((x) => x.trim())
      .map((x) => JSON.parse(x) as { id: number | null; error: { code: number } });
    expect(responses).toHaveLength(1);
    expect(responses[0]!.id).toBeNull();
    expect(responses[0]!.error.code).toBe(-32700);
  });

  it('真 node 子进程经 cli.ts `mcp` 子命令 stdio UTF-8 roundtrip（test_mcp_subprocess_utf8_roundtrip_without_ioencoding 对等）', async () => {
    // py 侧剥 PYTHONIOENCODING/PYTHONUTF8 验「claude 拉起」真实形态（win32 GBK 面）；node 管道
    // 无 locale 文本层（GBK 教训不存在），此处不注任何编码 env（铁规：探针不带掩盖性 env），
    // 以真子进程验 stdin Buffer 行读 + stdout utf8 写回的中文逐字节存活。
    // 入口 = cli.ts `mcp` 子命令（node ≥22.18 直跑 TS）——cmdline.mcpCommand 同款
    // `node …/src/cli.ts mcp` 形态，即 claude --mcp-config 真实拉起链路（内部同一 mcp.run()）。
    const cliPath = path.join(import.meta.dirname, '..', 'src', 'cli.ts');
    const proc = spawn(
      process.execPath,
      [
        cliPath,
        'mcp',
        '--agent-member',
        '01K5AGENT0000000000000000A',
        '--server-url',
        'http://127.0.0.1:1', // 不连——只验 stdio 编码面
        '--api-key',
        'cak_test',
      ],
      { stdio: ['pipe', 'pipe', 'pipe'] },
    );
    const outChunks: Buffer[] = [];
    // cal6：spawn 当拍同步挂 stdout/stderr 消费者；stderr 必须排空（type stripping 警告等）。
    proc.stdout.on('data', (c: Buffer) => outChunks.push(c));
    proc.stderr.resume();
    const closed = new Promise<void>((resolve) => proc.on('close', () => resolve()));
    try {
      const reqs = [
        {
          jsonrpc: '2.0',
          id: 1,
          method: 'initialize',
          params: {
            protocolVersion: '2024-11-05',
            capabilities: {},
            clientInfo: { name: '回归探针', version: '0' },
          },
        },
        { jsonrpc: '2.0', id: 2, method: 'tools/list' },
      ];
      const payload = reqs.map((r) => JSON.stringify(r)).join('\n') + '\n';
      proc.stdin.write(Buffer.from(payload, 'utf-8'));
      proc.stdin.end(); // EOF → serveStdio 退出 → run 返回
      await withTimeout(closed, 25_000);
    } finally {
      if (proc.exitCode === null && proc.pid !== undefined) {
        await killProcessTree(proc.pid); // 收尾必杀（铁规）；正常路径已自退
      }
    }
    const outText = Buffer.concat(outChunks).toString('utf-8');
    const lines = outText.split(/\r?\n/).filter((ln) => ln.trim());
    const responses = lines.map(
      (ln) => JSON.parse(ln) as { id: number; result?: { tools?: mcp.ToolDef[] } },
    );
    expect(responses.map((r) => r.id)).toEqual([1, 2]);
    const tools = responses[1]!.result!.tools!;
    const send = tools.find((t) => t.name === 'send_message')!;
    expect(send.description).toContain('唯一发言出口'); // 中文逐字节存活 = 双向 UTF-8 生效
  });

  // TS 增补（py 无对应用例；对应任务书「multipart 手拼上传对等（filename 中文编码对拍）」）：
  it('upload_file multipart 手拼与 py 逐字节同构（filename 中文原样 UTF-8 不转义）（TS 增补）', async () => {
    const tmp = mkTmp();
    const filePath = path.join(tmp, '中文名.txt');
    fs.writeFileSync(filePath, '文件内容 UTF-8', 'utf-8');
    const captured: { headers?: Record<string, string>; body?: Buffer } = {};
    const fakeFetch = async (
      _input: string | URL,
      init?: { headers?: Record<string, string>; body?: Buffer },
    ): Promise<Response> => {
      captured.headers = { ...init?.headers };
      captured.body = init?.body;
      return new Response('{"id":"F1"}', {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      });
    };
    vi.stubGlobal('fetch', fakeFetch);

    const http = mcp.makeFetchHttp('http://srv', 'k', 'M1');
    const out = await mcp.callTool('upload_file', { path: filePath }, http);
    expect(out.isError).toBe(false);

    const ctype = captured.headers!['Content-Type']!;
    expect(ctype.startsWith('multipart/form-data; boundary=----coagentia')).toBe(true);
    const boundary = ctype.split('boundary=')[1]!;
    const text = captured.body!.toString('utf-8');
    // py _multipart_file 同构：--boundary CRLF 头块 CRLF CRLF 内容 CRLF --boundary-- CRLF
    expect(text.startsWith(`--${boundary}\r\n`)).toBe(true);
    expect(text).toContain(
      'Content-Disposition: form-data; name="file"; filename="中文名.txt"\r\n',
    );
    expect(text).toContain('Content-Type: text/plain\r\n\r\n文件内容 UTF-8\r\n');
    expect(text.endsWith(`\r\n--${boundary}--\r\n`)).toBe(true);
  });
});
