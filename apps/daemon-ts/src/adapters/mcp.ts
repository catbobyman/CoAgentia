/**
 * coagentia stdio MCP server（契约 E §3）：Agent 一切主动行为的唯一出口。
 *
 * `coagentia-daemon mcp --agent-member <id> --server-url <url> --api-key <key>`
 * 由 claude 子进程经 --mcp-config 拉起（TS 侧：W4 cli.ts 挂 `mcp` 子命令调用本模块 run()）。
 * M1 最小工具集 → 契约 B REST 端点的**纯代理**（Bearer + X-Acting-Member）；
 * 权限门 / 护栏 / 留痕全部在 server 单点执法，MCP 层零业务规则。
 *
 * - send_message 命中 freshness → 202 held，本层**原样结构化透传**（M4 前不触发，形状先对）。
 * - 协议：newline-delimited JSON-RPC 2.0（initialize / tools/list / tools/call / ping）。
 * - HTTP 层可注入（测试用桩），真跑用 node 全局 fetch（py 侧 urllib；daemon 零 HTTP 依赖同款）。
 *
 * 对等基准 = apps/daemon adapters/mcp.py。
 * py→TS 差异（登记；非行为改进）：
 * - HTTP 注入面异步化：HttpFn 返回 Promise（py urllib 同步）；callTool / handleRpc / serveStdio
 *   相应 async。请求-响应一对一保序实现法：serveStdio 单消费循环**串行 await**——逐行
 *   handleLine（含 HTTP await）→ reply 写完才取下一行，与 py 同步循环同序，无并发窗口。
 * - stdio 行读按校准条款 2：Buffer 字节累积按 0x0a 切行 + CRLF 容忍 + **自设 32MB 行上限**
 *   （py 同步 readline 无上限；node 累积无界必设防 OOM）；超限行回 -32700 parse error 不崩循环。
 * - win32 GBK 教训（CR-M8-2 _reconfigure_stdio_utf8）在 node 不存在（管道无 locale 文本层）；
 *   写侧仍显式钉 utf8（cal1 规则 4 防御姿态），读侧整行一次解码（严禁逐 chunk toString）。
 * - «解析失败必回 parse error（id=null）不静默丢弃» 铁律逐字保留：请求丢了不回声，
 *   claude 侧对应 tools/call 会**无限等待**——wedge 教训「状态怎么出去」同族。
 */

import type { ContractKind, TaskStatus } from '@coagentia/contracts-ts';

import { randomBytes } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';

export type JsonObject = Record<string, unknown>;

export const MCP_PROTOCOL_VERSION = '2025-06-18';
export const SERVER_NAME = 'coagentia';
export const SERVER_VERSION = '1.0.0';

// 状态值域从契约派生（单一事实源）——py 为 [s.value for s in TaskStatus] 运行时派生；
// TS 契约包 type-only（裁决 #6）：字面量数组 + satisfies 锚成员合法性 + AssertNever 锚无漏值，
// 值序与 py 枚举定义序逐一致（手写字面量会在状态机演进时由 tsc 报错拦截漂移）。
type AssertNever<T extends never> = T;
const _TASK_STATUS_VALUES = [
  'todo',
  'in_progress',
  'in_review',
  'done',
  'closed',
] as const satisfies readonly TaskStatus[];
type _TaskStatusComplete = AssertNever<Exclude<TaskStatus, (typeof _TASK_STATUS_VALUES)[number]>>;
// 任务契约 kind 值域（submit_task_contract）：loop_contract 属 Reminder 域、端点会 422 拒，
// 故此处只列 POST /tasks/{id}/contracts 受理的两 kind（成员合法性由 satisfies 锚定，不求穷尽）。
const _CONTRACT_KIND_VALUES = ['task_plan', 'task_handoff'] as const satisfies readonly ContractKind[];

/** 工具 → REST 请求的中间表示（可脱离 HTTP 单测映射正确性）。 */
export interface ToolRequest {
  method: string;
  path: string; // 含 /api 前缀
  query: JsonObject | null;
  jsonBody: JsonObject | null;
  uploadPath: string | null; // upload_file：multipart 文件源
  download: boolean; // get_file：返回二进制元信息
}

export interface ToolResult {
  status: number;
  data: unknown;
  isError: boolean;
}

/** tools/call 结果形状（content[0].text = JSON 字符串）。 */
export interface CallToolResult {
  content: Array<{ type: string; text: string }>;
  isError: boolean;
}

/** py KeyError 对等（缺必填参 / 未知工具的抛出位；message = py str(KeyError) 同款带引号）。 */
export class KeyError extends Error {}

function toolRequest(
  method: string,
  reqPath: string,
  opts: Partial<Pick<ToolRequest, 'query' | 'jsonBody' | 'uploadPath' | 'download'>> = {},
): ToolRequest {
  return {
    method,
    path: reqPath,
    query: opts.query ?? null,
    jsonBody: opts.jsonBody ?? null,
    uploadPath: opts.uploadPath ?? null,
    download: opts.download ?? false,
  };
}

/** py `a[key]` 下标对等：键缺失抛 KeyError（callTool 收敛为 missing_argument）。 */
function requireArg(a: JsonObject, key: string): unknown {
  if (!(key in a)) {
    throw new KeyError(`'${key}'`);
  }
  return a[key];
}

/** py 真值语义（`if a.get(k):`）：None/False/0/''/空数组/空对象 皆假。 */
function pyTruthy(v: unknown): boolean {
  if (v === null || v === undefined || v === false || v === 0 || v === '') return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === 'object') return Object.keys(v as object).length > 0;
  return Boolean(v);
}

/** py `a.get(k) is not None` 对等（undefined 视同 py 键缺失 → None）。 */
function isNotNone(v: unknown): boolean {
  return v !== null && v !== undefined;
}

// ---- 工具描述用的可变 JSON schema 形状 ----

export interface ToolSchema {
  type: string;
  properties?: Record<string, JsonObject>;
  required?: string[];
}

export interface ToolDef {
  name: string;
  description: string;
  inputSchema: ToolSchema;
}

// ------------------------------------------------------------ 工具目录（M1 最小集，E §3）

export const TOOLS: ToolDef[] = [
  {
    name: 'send_message',
    description:
      '在频道发消息（唯一发言出口）。命中 freshness 时返回 202 held 结构，' +
      '此时停止重发、等待反馈。',
    inputSchema: {
      type: 'object',
      properties: {
        channel_id: { type: 'string' },
        body: { type: 'string' },
        thread_root_id: { type: 'string' },
        file_ids: { type: 'array', items: { type: 'string' } },
        as_task: {
          type: 'object',
          properties: { title: { type: 'string' } },
        },
      },
      required: ['channel_id', 'body'],
    },
  },
  {
    name: 'get_messages',
    description: '回看频道历史消息（投递批之外的按需拉取）。',
    inputSchema: {
      type: 'object',
      properties: {
        channel_id: { type: 'string' },
        limit: { type: 'integer' },
        before: { type: 'string' },
        after: { type: 'string' },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'get_thread',
    description: '拉取某消息所在线程的全部消息。',
    inputSchema: {
      type: 'object',
      properties: { message_id: { type: 'string' } },
      required: ['message_id'],
    },
  },
  {
    name: 'upload_file',
    description: '上传本地（Home 内）文件到 staging，返回 file_id。',
    inputSchema: {
      type: 'object',
      properties: { path: { type: 'string' } },
      required: ['path'],
    },
  },
  {
    name: 'get_file',
    description: '按 file_id 拉取文件内容元信息。',
    inputSchema: {
      type: 'object',
      properties: { file_id: { type: 'string' } },
      required: ['file_id'],
    },
  },
  {
    name: 'create_reminder',
    description:
      '创建提醒（recurring 缺 loop_contract → 422 原样透传）。' +
      'cadence：once = ISO 时刻；recurring = interval（ISO-8601 duration，如 PT1H）' +
      '或 cron 五段式（分 时 日 月 周，服务器本地时区）。' +
      'recurring 须内联 loop_contract 且其 cadence 与本 cadence 一致。',
    inputSchema: {
      type: 'object',
      properties: {
        kind: { type: 'string', enum: ['once', 'recurring'] },
        cadence: { type: 'string' },
        anchor_channel_id: { type: 'string' },
        anchor_message_id: { type: 'string' },
        anchor_task_id: { type: 'string' },
        loop_contract: {
          type: 'object',
          description: 'recurring 必填 LoopContract（PRD §4.3；随建即生效）。',
          properties: {
            version: { type: 'string' },
            // cadence 须与 reminder cadence 一致（interval 如 PT1H，或 cron 五段式）
            cadence: { type: 'string' },
            verification: { type: 'array', items: { type: 'string' } },
            budget: { type: 'object' },
            tools: { type: 'array', items: { type: 'string' } },
            escalation: { type: 'string' },
          },
        },
      },
      required: ['kind', 'cadence', 'anchor_channel_id'],
    },
  },
  {
    name: 'cancel_reminder',
    description: '取消一个提醒。',
    inputSchema: {
      type: 'object',
      properties: { reminder_id: { type: 'string' } },
      required: ['reminder_id'],
    },
  },
  {
    name: 'list_channels',
    description: '列出工作区频道（自我融入所需读面）。',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'list_members',
    description: '列出工作区成员（自我融入所需读面）。',
    inputSchema: { type: 'object', properties: {} },
  },
  // M2（契约 E v1.1）任务域 + 搜索
  {
    name: 'list_tasks',
    description: '列出任务（可按频道 / 状态 / owner / 创建者过滤，游标分页）。',
    inputSchema: {
      type: 'object',
      properties: {
        channel_id: { type: 'string' },
        status: { type: 'string', enum: [..._TASK_STATUS_VALUES] },
        owner: { type: 'string' },
        creator: { type: 'string' },
        after: { type: 'string' },
        limit: { type: 'integer' },
      },
    },
  },
  {
    name: 'get_task',
    description: '拉取单个任务详情（含成本聚合 usage）。',
    inputSchema: {
      type: 'object',
      properties: { task_id: { type: 'string' } },
      required: ['task_id'],
    },
  },
  {
    name: 'claim_task',
    description: '认领无主任务（并发抢占失败 → 409 CLAIM_RACE 结构化透传）。',
    inputSchema: {
      type: 'object',
      properties: { task_id: { type: 'string' } },
      required: ['task_id'],
    },
  },
  {
    name: 'unclaim_task',
    description: '释放自己认领的任务（仅本人为 owner 时有效）。',
    inputSchema: {
      type: 'object',
      properties: { task_id: { type: 'string' } },
      required: ['task_id'],
    },
  },
  {
    name: 'set_task_status',
    description: '推进任务状态（非法边 → 422 TASK_TRANSITION_INVALID 透传）。',
    inputSchema: {
      type: 'object',
      properties: {
        task_id: { type: 'string' },
        to: { type: 'string', enum: [..._TASK_STATUS_VALUES] },
      },
      required: ['task_id', 'to'],
    },
  },
  {
    name: 'search',
    description: '跨工作区搜索（频道 / 成员跳转 + 消息 FTS + 任务，三分组）。',
    inputSchema: {
      type: 'object',
      properties: {
        q: { type: 'string' },
        kind: { type: 'string', enum: ['message', 'task'] },
        from_member: { type: 'string' },
        in_channel: { type: 'string' },
        limit: { type: 'integer' },
      },
      required: ['q'],
    },
  },
  // M7（契约 E v1.5）部署——R8「部署全员含 Agent」的通道兑现
  {
    name: 'trigger_deploy',
    description:
      '触发一次部署（全员含 Agent，R8）。请求体空——' +
      '分支/commit 由 server 触发时解析主干 HEAD。' +
      '进行中→409 DEPLOY_IN_PROGRESS / 无 deploy_command→422 / daemon 离线→503，均结构化透传。' +
      '部署结果经绑定频道的结果卡消息被动触达（无需轮询）。',
    inputSchema: {
      type: 'object',
      properties: { project_id: { type: 'string' } },
      required: ['project_id'],
    },
  },
  // M8-B5（契约 E v1.6）契约提交——置任务 in_review/done 的前置通道
  {
    name: 'submit_task_contract',
    description:
      '提交/修订任务契约（置任务 in_review/done 的前置——T7 门要求活动 ' +
      'TaskHandoff 的 deliverables/evidence 非空，缺则 set_task_status 以 422 ' +
      'HANDOFF_INCOMPLETE 退回）。\n' +
      'kind=task_handoff（完成实现/评审后的跨 Agent 交接）字段：' +
      "version='coagentia.task-handoff.v1'、from_member（你的 member_id）、" +
      'to_member（接收方 member_id：评审人/人类/下游）、' +
      'deliverables=[{path,kind}]（置 in_review 前须≥1）、' +
      'evidence=[{type,ref,conclusion}]、verify_plan（接收方如何独立复核）、' +
      'open_risks=[]（可空）、review_verdict（可空）。\n' +
      "kind=task_plan（立项/升格计划）字段：version='coagentia.task-plan.v1'、goal、" +
      'acceptance_criteria=[{id,statement,verify_by,verify_ref}]（≥1）、' +
      'defaults_decided=[]、out_of_scope=[]。\n' +
      '字段不符 → 422 VALIDATION_FAILED 携逐字段 loc/msg，按清单补齐重投即可（同 kind ' +
      '重复提交自动 supersede 成修订链，不新建重复行）。',
    inputSchema: {
      type: 'object',
      properties: {
        task_id: { type: 'string' },
        kind: { type: 'string', enum: [..._CONTRACT_KIND_VALUES] },
        body: {
          type: 'object',
          description:
            '契约内容，按 kind 对应 TaskHandoffBody / TaskPlanBody' +
            '（server 二次 model_validate，字段见上）。',
        },
      },
      required: ['task_id', 'kind', 'body'],
    },
  },
  // DEDAG（契约 E v1.7）委派/合并——去画布编排后 Orchestrator 对话式派活的行为通道
  {
    name: 'create_task',
    description:
      '派活：以你的名义在频道发锚点消息并**原子转任务**（委派的唯一通道）。' +
      '建议负责人直接在正文 text 里写 @名字——mention 即唤醒对方；不设 owner，' +
      '认领仍走 claim 防重。writes_code=true（任务要写代码、需 worktree）时**必须**携带' +
      '本频道绑定项目的 project_id，否则 422；纯讨论/文档类任务不带 writes_code 即可。' +
      '成功返回 message+task（task_id 从 data.task.id 取）。',
    inputSchema: {
      type: 'object',
      properties: {
        channel_id: { type: 'string' },
        text: {
          type: 'string',
          description: '锚点消息正文（任务背景/要求；建议负责人用 @名字 写进正文）。',
        },
        title: {
          type: 'string',
          description: '任务标题（缺省由 server 取缺省标题）。',
        },
        project_id: {
          type: 'string',
          description: 'writes_code=true 时必填 = 本频道绑定项目的 id。',
        },
        writes_code: {
          type: 'boolean',
          description: '任务是否要写代码（默认 false；true 由 server 建 worktree）。',
        },
      },
      required: ['channel_id', 'text'],
    },
  },
  {
    name: 'trigger_merge',
    description:
      '把已完成任务的 worktree 合并回主干（请求体空，合并计划由 server 解析）。' +
      '202 受理：status=accepted=已受理异步执行，结果以频道系统消息回报（勿轮询勿重发）；' +
      'status=merged=该任务早已合并、幂等命中无需再动。' +
      '409 DEPLOY_IN_PROGRESS=同项目已有合并在跑、稍后重试；503=daemon 离线。' +
      '合并冲突时 server 会自动创建冲突解决任务派回原 owner，无需你介入。',
    inputSchema: {
      type: 'object',
      properties: { task_id: { type: 'string' } },
      required: ['task_id'],
    },
  },
];

const _TOOL_NAMES = new Set(TOOLS.map((t) => t.name));

// freshness 202 held 仅对消息发送面成立（send_message / create_task 同端点同护栏）；
// trigger_merge 的 202 是 TaskMergeAccepted 受理回执，误标 held 会让 Agent 误停等待。
const _FRESHNESS_HELD_TOOLS = new Set(['send_message', 'create_task']);

/** 工具调用参数 → REST 请求（契约 B 端点，每工具一一对应，不发明无端点工具）。 */
export function buildRequest(tool: string, args: JsonObject | null | undefined): ToolRequest {
  const a: JsonObject = args ?? {};
  if (tool === 'send_message') {
    const body: JsonObject = { body: 'body' in a ? a['body'] : '' };
    if (pyTruthy(a['thread_root_id'])) {
      body['thread_root_id'] = a['thread_root_id'];
    }
    if (pyTruthy(a['file_ids'])) {
      body['file_ids'] = a['file_ids'];
    }
    if (isNotNone(a['as_task'])) {
      // 空 {} 也透传 → server 用缺省 title（契约 AsTask）
      body['as_task'] = a['as_task'];
    }
    return toolRequest('POST', `/api/channels/${String(requireArg(a, 'channel_id'))}/messages`, {
      jsonBody: body,
    });
  }
  if (tool === 'get_messages') {
    const query: JsonObject = {};
    for (const k of ['limit', 'before', 'after']) {
      if (isNotNone(a[k])) query[k] = a[k];
    }
    return toolRequest('GET', `/api/channels/${String(requireArg(a, 'channel_id'))}/messages`, {
      query: Object.keys(query).length > 0 ? query : null,
    });
  }
  if (tool === 'get_thread') {
    return toolRequest('GET', `/api/messages/${String(requireArg(a, 'message_id'))}/thread`);
  }
  if (tool === 'upload_file') {
    return toolRequest('POST', '/api/files', { uploadPath: String(requireArg(a, 'path')) });
  }
  if (tool === 'get_file') {
    return toolRequest('GET', `/api/files/${String(requireArg(a, 'file_id'))}/content`, {
      download: true,
    });
  }
  if (tool === 'create_reminder') {
    const fields = [
      'kind',
      'cadence',
      'anchor_channel_id',
      'anchor_message_id',
      'anchor_task_id',
      'loop_contract',
    ];
    const body: JsonObject = {};
    for (const k of fields) {
      if (isNotNone(a[k])) body[k] = a[k];
    }
    return toolRequest('POST', '/api/reminders', { jsonBody: body });
  }
  if (tool === 'cancel_reminder') {
    return toolRequest('DELETE', `/api/reminders/${String(requireArg(a, 'reminder_id'))}`);
  }
  if (tool === 'list_channels') {
    return toolRequest('GET', '/api/channels');
  }
  if (tool === 'list_members') {
    return toolRequest('GET', '/api/members');
  }
  if (tool === 'list_tasks') {
    const query: JsonObject = {};
    for (const k of ['channel_id', 'status', 'owner', 'creator', 'after', 'limit']) {
      if (isNotNone(a[k])) query[k] = a[k];
    }
    return toolRequest('GET', '/api/tasks', {
      query: Object.keys(query).length > 0 ? query : null,
    });
  }
  if (tool === 'get_task') {
    return toolRequest('GET', `/api/tasks/${String(requireArg(a, 'task_id'))}`);
  }
  if (tool === 'claim_task') {
    return toolRequest('POST', `/api/tasks/${String(requireArg(a, 'task_id'))}/claim`);
  }
  if (tool === 'unclaim_task') {
    return toolRequest('POST', `/api/tasks/${String(requireArg(a, 'task_id'))}/unclaim`);
  }
  if (tool === 'set_task_status') {
    return toolRequest('POST', `/api/tasks/${String(requireArg(a, 'task_id'))}/status`, {
      jsonBody: { to: requireArg(a, 'to') },
    });
  }
  if (tool === 'search') {
    // py 此处 query 恒传 dict（可为空 {}，不收敛为 None）——与 get_messages/list_tasks 不同，原样保留。
    const query: JsonObject = {};
    for (const k of ['q', 'kind', 'from_member', 'in_channel', 'limit']) {
      if (isNotNone(a[k])) query[k] = a[k];
    }
    return toolRequest('GET', '/api/search', { query });
  }
  if (tool === 'trigger_deploy') {
    // 空请求体：分支/commit 由 server 解析主干 HEAD
    return toolRequest('POST', `/api/projects/${String(requireArg(a, 'project_id'))}/deployments`);
  }
  if (tool === 'submit_task_contract') {
    // body free-form 透传，server 按 kind 二次校验
    return toolRequest('POST', `/api/tasks/${String(requireArg(a, 'task_id'))}/contracts`, {
      jsonBody: { kind: requireArg(a, 'kind'), body: requireArg(a, 'body') },
    });
  }
  if (tool === 'create_task') {
    // 锚点消息 + 转任务复合（as_task 语义 = 契约 B §9.4 既有）
    const asTask: JsonObject = {};
    for (const k of ['title', 'project_id', 'writes_code']) {
      if (isNotNone(a[k])) asTask[k] = a[k];
    }
    return toolRequest('POST', `/api/channels/${String(requireArg(a, 'channel_id'))}/messages`, {
      jsonBody: { body: requireArg(a, 'text'), as_task: asTask },
    });
  }
  if (tool === 'trigger_merge') {
    // 空请求体：合并计划（worktree/分支）由 server 按任务解析
    return toolRequest('POST', `/api/tasks/${String(requireArg(a, 'task_id'))}/merge`);
  }
  throw new KeyError(`'${tool}'`);
}

// HTTP 执行口（(ToolRequest) -> Promise<ToolResult>），真跑用 fetch，测试注入桩。
export type HttpFn = (req: ToolRequest) => Promise<ToolResult>;

/**
 * 执行工具 → MCP tools/call result（content[0].text = JSON 字符串）。
 *
 * 202 held / 4xx / 422 一律**原样结构化透传**（含 status），Agent 据此感知被扣/失败。
 */
export async function callTool(
  tool: string,
  args: JsonObject | null | undefined,
  http: HttpFn,
): Promise<CallToolResult> {
  if (!_TOOL_NAMES.has(tool)) {
    return textResult({ error: 'unknown_tool', tool }, true);
  }
  let request: ToolRequest;
  try {
    request = buildRequest(tool, args);
  } catch (exc) {
    if (exc instanceof KeyError) {
      return textResult({ error: 'missing_argument', detail: exc.message }, true);
    }
    throw exc;
  }
  const res = await http(request);
  const payload: JsonObject = { status: res.status, data: res.data };
  if (res.status === 202 && _FRESHNESS_HELD_TOOLS.has(tool)) {
    payload['held'] = true; // freshness 命中：Agent 停止重发、等待反馈直投（D §5.2）
  }
  const isError = res.isError || res.status >= 400;
  return textResult(payload, isError);
}

function textResult(obj: unknown, isError = false): CallToolResult {
  // py json.dumps(ensure_ascii=False) ≡ JSON.stringify（非 ASCII 不转义；分隔符空格差异语义等价）
  return { content: [{ type: 'text', text: JSON.stringify(obj) }], isError };
}

// ------------------------------------------------------------ 真 HTTP（fetch）

/** py utf-8 严格解码对等：无效字节抛错（Buffer.toString 会静默 U+FFFD，不可用于此处）。 */
function decodeUtf8Strict(raw: Uint8Array): string {
  return new TextDecoder('utf-8', { fatal: true }).decode(raw);
}

/**
 * 真 HTTP 执行器（py make_urllib_http 对等；TS 用 node 全局 fetch，导出名相应更名）。
 *
 * 测试注入面：py 侧 monkeypatch urllib.request.urlopen ↔ TS 侧 vi.stubGlobal('fetch', …)
 * （本函数每次调用时解析全局 fetch，不在闭包捕获）。
 */
export function makeFetchHttp(serverUrl: string, apiKey: string, actingMember: string): HttpFn {
  const base = serverUrl.replace(/\/+$/, ''); // py rstrip('/')

  return async (req: ToolRequest): Promise<ToolResult> => {
    let url = base + req.path;
    if (pyTruthy(req.query)) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(req.query as JsonObject)) {
        qs.append(k, String(v));
      }
      // py urlencode 与 URLSearchParams 同为 form-urlencoded（空格→+）；个别保留字符
      // （如 *）两侧百分号编码略有出入，server 解析语义等价（登记差异）。
      url += '?' + qs.toString();
    }
    const headers: Record<string, string> = {
      Authorization: `Bearer ${apiKey}`,
      'X-Acting-Member': actingMember,
    };
    let data: Buffer | null = null;
    if (req.uploadPath !== null) {
      const [payload, uploadCtype] = multipartFile(req.uploadPath);
      data = payload;
      headers['Content-Type'] = uploadCtype;
    } else if (req.jsonBody !== null) {
      // py json.dumps 默认 ensure_ascii=True（非 ASCII 转 \uXXXX）；JSON.stringify 原样 UTF-8。
      // 两者是同一 JSON 值的合法编码，server 解析等价（登记差异）。
      data = Buffer.from(JSON.stringify(req.jsonBody), 'utf-8');
      headers['Content-Type'] = 'application/json';
    }
    let status: number;
    let raw: Buffer;
    let ctype: string;
    try {
      const resp = await fetch(url, {
        method: req.method,
        headers,
        body: data,
        signal: AbortSignal.timeout(30_000), // py urlopen(timeout=30)
      });
      // fetch 对 4xx/5xx 不抛（py HTTPError 分支在此合流：同样读 status/body/头）。
      status = resp.status;
      raw = Buffer.from(await resp.arrayBuffer());
      ctype = resp.headers.get('Content-Type') ?? '';
    } catch (exc) {
      // 网络异常收敛为 isError（不崩 MCP 进程）；detail 文案 = String(exc)（py repr(exc)，登记差异）。
      return { status: 0, data: { error: 'http_error', detail: String(exc) }, isError: true };
    }
    if (req.download) {
      // py 此分支不置 is_error（callTool 层按 status>=400 补判）——原样保留。
      return { status, data: { size_bytes: raw.length, mime: ctype || null }, isError: false };
    }
    const parsed = parseBody(raw, ctype);
    return { status, data: parsed, isError: status >= 400 };
  };
}

function parseBody(raw: Buffer, ctype: string): unknown {
  if (raw.length === 0) {
    return null;
  }
  if (ctype.includes('json')) {
    try {
      return JSON.parse(decodeUtf8Strict(raw));
    } catch {
      // py 同款 pass：JSON 解析失败落到纯文本分支
    }
  }
  try {
    return decodeUtf8Strict(raw);
  } catch {
    return { bytes: raw.length };
  }
}

// py mimetypes.guess_type 的手写近似（node 无内置 mime 表且零运行时依赖，登记差异）：
// 常见扩展名内联，未知回退 application/octet-stream（py 侧平台相关本就不确定）。
const _MIME_TYPES: Record<string, string> = {
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.json': 'application/json',
  '.html': 'text/html',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.csv': 'text/csv',
  '.xml': 'text/xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.pdf': 'application/pdf',
  '.zip': 'application/zip',
};

/**
 * multipart/form-data 手拼（py _multipart_file 逐字节同构）：
 * `--boundary\r\n Content-Disposition(filename 原样 UTF-8 不转义)\r\n Content-Type\r\n\r\n
 * 文件字节 \r\n --boundary--\r\n`。boundary = ----coagentia + 32 位十六进制随机
 * （py uuid4().hex ↔ randomBytes(16).hex，形状等价）。
 */
function multipartFile(filePath: string): [Buffer, string] {
  const boundary = `----coagentia${randomBytes(16).toString('hex')}`;
  const name = path.basename(filePath);
  const mime = _MIME_TYPES[path.extname(name).toLowerCase()] ?? 'application/octet-stream';
  const content = fs.readFileSync(filePath);
  const pre = Buffer.from(
    `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="${name}"\r\n` +
      `Content-Type: ${mime}\r\n\r\n`,
    'utf-8',
  );
  const post = Buffer.from(`\r\n--${boundary}--\r\n`, 'utf-8');
  return [Buffer.concat([pre, content, post]), `multipart/form-data; boundary=${boundary}`];
}

// ------------------------------------------------------------ JSON-RPC stdio 循环

export class _RpcState {
  http: HttpFn;
  initialized = false;
  extraLog: string[] = [];

  constructor(http: HttpFn) {
    this.http = http;
  }
}

/** 处理一条 JSON-RPC 请求 → 响应对象（notification 返回 null）。 */
export async function handleRpc(msg: JsonObject, state: _RpcState): Promise<JsonObject | null> {
  const method = msg['method'];
  const mid = msg['id'] ?? null; // py .get("id")：键缺失与显式 null 同归 None
  if (method === 'initialize') {
    return _ok(mid, {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
    });
  }
  if (method === 'notifications/initialized' || method === 'initialized') {
    state.initialized = true;
    return null;
  }
  if (method === 'ping') {
    return _ok(mid, {});
  }
  if (method === 'tools/list') {
    return _ok(mid, { tools: TOOLS });
  }
  if (method === 'tools/call') {
    const params = (msg['params'] as JsonObject | null | undefined) ?? {};
    const name = (params['name'] as string | null | undefined) ?? '';
    const args = (params['arguments'] as JsonObject | null | undefined) ?? {};
    return _ok(mid, await callTool(name, args, state.http));
  }
  if (mid === null) {
    return null; // 未知 notification → 忽略
  }
  return _err(mid, -32601, `method not found: ${String(method)}`);
}

function _ok(mid: unknown, result: unknown): JsonObject {
  return { jsonrpc: '2.0', id: mid, result };
}

function _err(mid: unknown, code: number, message: string): JsonObject {
  return { jsonrpc: '2.0', id: mid, error: { code, message } };
}

/** 校准条款 2：行上限自设 32MB（node 累积无界防 OOM；py 同步 readline 无对应物）。 */
export const MAX_LINE_BYTES = 32 * 1024 * 1024;

/** 可注入 stdout 面（process.stdout 结构兼容；once 存在时按 drain 背压，cal6 同款）。 */
export interface StdoutLike {
  write(chunk: string): unknown;
  once?(event: 'drain', listener: () => void): unknown;
}

/**
 * newline-delimited JSON-RPC 循环（stdin EOF → 退出；py 同步循环 → TS 串行 async 循环）。
 *
 * 解析失败必须回 JSON-RPC parse error（id=null）而非静默丢弃（CR-M8-2）：请求丢了不回声，
 * claude 侧对应 tools/call 会**无限等待**——wedge 教训「状态怎么出去」同族。
 *
 * 保序：逐行 await（handleLine 内含 HTTP await 与 reply 写回）后才消费下一行——
 * 响应严格按请求序写出，与 py 同步循环等价（无并发处理窗口）。
 */
export async function serveStdio(
  http: HttpFn,
  stdin?: AsyncIterable<Buffer | string> | Iterable<Buffer | string> | null,
  stdout?: StdoutLike | null,
): Promise<void> {
  const rin = stdin ?? (process.stdin as AsyncIterable<Buffer>);
  const rout: StdoutLike = stdout ?? process.stdout;
  const state = new _RpcState(http);

  const reply = async (obj: JsonObject): Promise<void> => {
    const flushed = rout.write(JSON.stringify(obj) + '\n');
    if (flushed === false && typeof rout.once === 'function') {
      // 背压：write false → 等 drain（cal6 stdin write→drain 同款；py flush() 对等的送达保证）
      await new Promise<void>((resolve) => {
        rout.once!('drain', resolve);
      });
    }
  };

  const handleLine = async (lineBuf: Buffer): Promise<void> => {
    if (lineBuf.length > MAX_LINE_BYTES) {
      await reply(_err(null, -32700, `parse error: line exceeds ${MAX_LINE_BYTES} bytes`));
      return;
    }
    // 整行一次解码（cal1 规则 4：严禁逐 chunk toString）；trim ≡ py strip（CRLF 容忍）。
    const line = lineBuf.toString('utf-8').trim();
    if (!line) {
      return;
    }
    let msg: unknown;
    try {
      msg = JSON.parse(line);
    } catch (exc) {
      await reply(
        _err(null, -32700, `parse error: ${exc instanceof Error ? exc.message : String(exc)}`),
      );
      return;
    }
    const response = await handleRpc((msg ?? {}) as JsonObject, state);
    if (response !== null) {
      await reply(response);
    }
  };

  // 累积法 = parts[] 段引用聚合（同 codex LineReader 修复族）：逐 chunk 只存引用、按完行才
  // concat 一次——原「每 chunk 与未终止前缀整段 Buffer.concat」在长行/多 chunk 下是 O(n²) 拷贝。
  // 不变量：parts 内不含 0x0a（换行只可能出现在新到 chunk 中）。
  let parts: Buffer[] = [];
  let partBytes = 0;
  let skipping = false; // 超限行已回 parse error，弃字节直到下一换行
  for await (const chunkRaw of rin) {
    let chunk = typeof chunkRaw === 'string' ? Buffer.from(chunkRaw, 'utf-8') : chunkRaw;
    if (skipping) {
      const nl = chunk.indexOf(0x0a);
      if (nl === -1) {
        continue; // 仍在超限行内：整块丢弃（防 OOM）
      }
      chunk = chunk.subarray(nl + 1);
      skipping = false;
    }
    let idx: number;
    while ((idx = chunk.indexOf(0x0a)) !== -1) {
      const head = chunk.subarray(0, idx);
      chunk = chunk.subarray(idx + 1);
      let lineBuf: Buffer;
      if (parts.length === 0) {
        lineBuf = head;
      } else {
        parts.push(head);
        lineBuf = Buffer.concat(parts); // 完行才 concat 恰一次
        parts = [];
        partBytes = 0;
      }
      await handleLine(lineBuf);
    }
    if (chunk.length > 0) {
      parts.push(chunk);
      partBytes += chunk.length;
    }
    if (partBytes > MAX_LINE_BYTES) {
      await reply(_err(null, -32700, `parse error: line exceeds ${MAX_LINE_BYTES} bytes`));
      parts = [];
      partBytes = 0;
      skipping = true;
    }
  }
  if (!skipping && partBytes > 0) {
    // py `for line in rin` 对末尾无换行行同样产出
    await handleLine(parts.length === 1 ? parts[0]! : Buffer.concat(parts));
  }
}

/**
 * stdio 编码钉桩（py _reconfigure_stdio_utf8 对等位；CR-M8-2 家族）。
 *
 * win32 GBK 缺陷根源是 py TextIO 对管道默认 locale 编码——node 管道是纯字节流、无该层，
 * 缺陷在 node 不存在。读侧由 serveStdio 按 Buffer 累积整行 utf8 解码；写侧仍显式钉 utf8
 * （cal1 规则 4 的防御姿态，防运行环境改写默认编码）。
 */
function _reconfigureStdioUtf8(): void {
  process.stdout.setDefaultEncoding('utf-8');
}

/** `coagentia-daemon mcp` 入口（W4 cli.ts `mcp` 子命令调用）。 */
export async function run(agentMemberId: string, serverUrl: string, apiKey: string): Promise<number> {
  _reconfigureStdioUtf8();
  const http = makeFetchHttp(serverUrl, apiKey, agentMemberId);
  await serveStdio(http);
  return 0;
}
