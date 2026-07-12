// decomposition.ts / fingerprint.ts 与 server 拆解校验内核（packages/contracts kernel/decomposition.py）
// 平价：加载同一组黄金判例（packages/fixtures/golden/decomposition.json）逐条断言（含 code/path/message/
// hint 文本、遍历顺序、指纹哈希），防前后端双实现漂移（纪律 8）。运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';

import golden from '../../../../packages/fixtures/golden/decomposition.json';
import {
  parseControl,
  proposalFingerprint,
  validateProposal,
  type DecompEnv,
  type DecompError,
} from './decomposition';

interface ValidateCase {
  fn: 'validate_proposal';
  name: string;
  env: DecompEnv;
  body: Record<string, unknown>;
  errors: DecompError[];
}
interface ParseCase {
  fn: 'parse_control';
  name: string;
  text: string;
  ok: boolean;
  body?: Record<string, unknown>;
  error?: DecompError;
}
interface FingerprintCase {
  fn: 'proposal_fingerprint';
  name: string;
  body: Record<string, unknown>;
  hash: string;
}
type GoldenCase = ValidateCase | ParseCase | FingerprintCase;

const cases = golden as unknown as GoldenCase[];
const validateCases = cases.filter((c): c is ValidateCase => c.fn === 'validate_proposal');
const parseCases = cases.filter((c): c is ParseCase => c.fn === 'parse_control');
const fpCases = cases.filter((c): c is FingerprintCase => c.fn === 'proposal_fingerprint');

describe('validateProposal 黄金判例平价', () => {
  it('golden 含 validate_proposal 判例', () => {
    expect(validateCases.length).toBeGreaterThan(0);
  });
  for (const c of validateCases) {
    it(`validate_proposal: ${c.name}`, () => {
      // 逐字节对照（code/path/message/hint 文本 + 顺序，与 server 镜像可比）。
      expect(validateProposal(c.body, c.env)).toEqual(c.errors);
    });
  }
});

describe('parseControl 黄金判例平价', () => {
  it('golden 含 parse_control 判例', () => {
    expect(parseCases.length).toBeGreaterThan(0);
  });
  for (const c of parseCases) {
    it(`parse_control: ${c.name}`, () => {
      const { body, error } = parseControl(c.text);
      expect(error === null).toBe(c.ok);
      if (c.ok) {
        expect(body).toEqual(c.body);
      } else {
        expect(error).toEqual(c.error);
      }
    });
  }
});

describe('proposalFingerprint 黄金判例平价', () => {
  it('golden 含 proposal_fingerprint 判例', () => {
    expect(fpCases.length).toBeGreaterThan(0);
  });
  for (const c of fpCases) {
    it(`proposal_fingerprint: ${c.name}`, () => {
      expect(proposalFingerprint(c.body)).toBe(c.hash);
    });
  }
});

// ---- 边界补充（超出 golden 的镜像自证）----
const ENV: DecompEnv = { node_limit: 12, member_ids: ['M1', 'M2'], bound_project_ids: ['P1'] };

describe('parseControl 围栏容忍与多块', () => {
  it('markdown 围栏（``` / ```json / ~~~）包裹仍识别', () => {
    for (const fence of ['```', '```json', '~~~']) {
      const text = `说明\n${fence}\n<control>{"a":1}</control>\n\`\`\``;
      const { body, error } = parseControl(text);
      expect(error).toBeNull();
      expect(body).toEqual({ a: 1 });
    }
  });
  it('围栏内外重复出现同一块 → 多块错误', () => {
    const { error } = parseControl('<control>{}</control>\n```\n<control>{}</control>\n```');
    expect(error?.code).toBe('CONTROL_PARSE');
    expect(error?.message).toContain('2');
  });
  it('残缺开标签（无闭合）算缺块', () => {
    const { body, error } = parseControl('<control>{"a":1}');
    expect(body).toBeNull();
    expect(error).not.toBeNull();
  });
});

describe('validateProposal 边界', () => {
  it('非对象 body → 单条 FIELD_INVALID', () => {
    expect(validateProposal([], ENV)).toEqual([
      { code: 'FIELD_INVALID', path: '$', message: '提案必须为 JSON 对象' },
    ]);
  });
  it('node_limit 来自 env（超限 NODE_COUNT / 恰好通过）', () => {
    const mk = (n: number): Record<string, unknown> => ({
      version: 'coagentia.decomposition.v1',
      source: 'T',
      mode: 'decompose',
      summary: 's',
      nodes: Array.from({ length: n }, (_, i) => ({
        temp_id: `N${i}`,
        title: `t${i}`,
        kind: 'agent',
        task_plan: {
          goal: 'g',
          acceptance_criteria: [{ id: 'AC1', statement: 's', verify_by: 'manual', verify_ref: '' }],
        },
      })),
      edges: [],
    });
    const codes = (errs: DecompError[]): Set<string> => new Set(errs.map((e) => e.code));
    expect(codes(validateProposal(mk(3), { ...ENV, node_limit: 2 })).has('NODE_COUNT')).toBe(true);
    expect(validateProposal(mk(3), { ...ENV, node_limit: 3 })).toEqual([]);
  });
  it('ref 语义 = id 精确匹配（大小写不同即非成员）', () => {
    const body = {
      version: 'coagentia.decomposition.v1',
      source: 'T',
      mode: 'single_task',
      summary: 's',
      nodes: [
        {
          temp_id: 'N1',
          title: 't',
          kind: 'agent',
          suggested_owner: 'm1',
          task_plan: {
            goal: 'g',
            acceptance_criteria: [
              { id: 'AC1', statement: 's', verify_by: 'command', verify_ref: 'x' },
            ],
          },
        },
      ],
    };
    expect(new Set(validateProposal(body, ENV).map((e) => e.code)).has('OWNER_NOT_MEMBER')).toBe(
      true,
    );
  });
});

// 单节点 single_task 基底（深层严格度/码点边界测试用）
function singleWith(
  node: Record<string, unknown>,
  summary = 's',
): Record<string, unknown> {
  return {
    version: 'coagentia.decomposition.v1',
    source: 'T',
    mode: 'single_task',
    summary,
    nodes: [node],
  };
}
function agentNode(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    temp_id: 'N1',
    title: 't',
    kind: 'agent',
    task_plan: {
      goal: 'g',
      acceptance_criteria: [{ id: 'AC1', statement: 's', verify_by: 'manual', verify_ref: '' }],
    },
    ...extra,
  };
}

describe('validateProposal 深层严格度（kernel ≥ TaskPlanBody 消费）', () => {
  it('AC 缺 id/verify_ref → AC_INVALID path 精确到字段；空串不禁', () => {
    const node = agentNode();
    (node['task_plan'] as Record<string, unknown>)['acceptance_criteria'] = [
      { statement: 's', verify_by: 'manual' },
    ];
    const paths = validateProposal(singleWith(node), ENV)
      .filter((e) => e.code === 'AC_INVALID')
      .map((e) => e.path);
    expect(paths).toContain('$.nodes[0].task_plan.acceptance_criteria[0].id');
    expect(paths).toContain('$.nodes[0].task_plan.acceptance_criteria[0].verify_ref');
    // 空串合法（TaskPlanBody 不禁）
    const node2 = agentNode();
    (node2['task_plan'] as Record<string, unknown>)['acceptance_criteria'] = [
      { id: '', statement: '', verify_by: 'manual', verify_ref: '' },
    ];
    expect(validateProposal(singleWith(node2), ENV)).toEqual([]);
  });
  it('未知字段执法到 task_plan/AC 层（无别名 hint）', () => {
    const node = agentNode();
    const plan = node['task_plan'] as Record<string, unknown>;
    plan['estimate'] = 5;
    (plan['acceptance_criteria'] as Array<Record<string, unknown>>)[0]!['note'] = 'x';
    const unknown = validateProposal(singleWith(node), ENV).filter(
      (e) => e.code === 'UNKNOWN_FIELD',
    );
    expect(unknown.map((e) => e.path)).toEqual([
      '$.nodes[0].task_plan.estimate',
      '$.nodes[0].task_plan.acceptance_criteria[0].note',
    ]);
    expect(unknown.every((e) => e.hint === undefined)).toBe(true);
  });
  it('task_plan.version 错值红、正确/缺席绿；defaults 须字符串数组', () => {
    const bad = agentNode();
    (bad['task_plan'] as Record<string, unknown>)['version'] = 'coagentia.task-plan.v2';
    expect(
      validateProposal(singleWith(bad), ENV).some(
        (e) => e.code === 'FIELD_INVALID' && e.path === '$.nodes[0].task_plan.version',
      ),
    ).toBe(true);
    const good = agentNode();
    const gp = good['task_plan'] as Record<string, unknown>;
    gp['version'] = 'coagentia.task-plan.v1';
    gp['defaults_decided'] = ['用 SQLite'];
    gp['out_of_scope'] = [];
    expect(validateProposal(singleWith(good), ENV)).toEqual([]);
    const badArr = agentNode();
    (badArr['task_plan'] as Record<string, unknown>)['defaults_decided'] = 'x';
    expect(
      validateProposal(singleWith(badArr), ENV).some(
        (e) => e.path === '$.nodes[0].task_plan.defaults_decided',
      ),
    ).toBe(true);
  });
});

describe('validateProposal 长度语义 = Unicode 码点（缺口 B）', () => {
  it('title 含增补平面字符恰 80 码点（UTF-16 码元 81）→ 绿', () => {
    // .length 会数出 81 而误红——码点计数是两侧一致的唯一正解
    const node = agentNode({ title: '甲'.repeat(79) + '🍅' });
    expect(validateProposal(singleWith(node, 's'.repeat(199) + '🍅'), ENV)).toEqual([]);
  });
  it('title 81 码点 → 红', () => {
    const node = agentNode({ title: '甲'.repeat(80) + '🍅' });
    expect(
      validateProposal(singleWith(node), ENV).some(
        (e) => e.code === 'FIELD_INVALID' && e.path === '$.nodes[0].title',
      ),
    ).toBe(true);
  });
});

describe('proposalFingerprint 指纹哈希（纯 JS SHA-256 与 Python hashlib 对齐）', () => {
  it('剔除系统注入字段后与净体同指纹；书写序无关', () => {
    const clean = fpCases.find((c) => c.name === 'fp_clean');
    const injected = fpCases.find((c) => c.name === 'fp_strips_system_fields');
    const shuffled = fpCases.find((c) => c.name === 'fp_order_invariant');
    expect(clean && injected && shuffled).toBeTruthy();
    expect(proposalFingerprint(clean!.body)).toBe(proposalFingerprint(injected!.body));
    expect(proposalFingerprint(clean!.body)).toBe(proposalFingerprint(shuffled!.body));
  });
  it('输出 64 位小写 hex', () => {
    const h = proposalFingerprint(fpCases[0]!.body);
    expect(h).toMatch(/^[0-9a-f]{64}$/);
  });
});
