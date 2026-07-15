// P5 契约卡渲染行为(M3 B-M3-1):TaskPlan/TaskHandoff 按真 TaskContractPublic 形状渲染、
// revision/superseded 修订链、空契约占位文案不再提"M3 接入"。
// 运行:pnpm -F @coagentia/web test
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type {
  MemberPublic,
  TaskContractPublic,
  TaskDetail,
  TaskHandoffBody,
  TaskPlanBody,
  TaskPublic,
} from '@coagentia/contracts-ts';

import { qk } from '../lib/queryKeys';
import { ToastProvider } from '../components/Toast';
import { ThreadPanel } from './ThreadPanel';

const OWNER: MemberPublic = {
  id: 'mem_owner', kind: 'human', role: 'owner', name: 'Memcyo',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const HANK: MemberPublic = {
  id: 'mem_hank', kind: 'agent', name: 'Hank',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const PAT: MemberPublic = {
  id: 'mem_pat', kind: 'agent', name: 'Pat',
  workspace_id: 'ws_1', created_at: '2026-07-09T00:00:00Z',
};
const MEMBER_BY_ID: Record<string, MemberPublic> = {
  [OWNER.id]: OWNER, [HANK.id]: HANK, [PAT.id]: PAT,
};

const TASK: TaskPublic = {
  id: 'task_1',
  channel_id: 'ch_build',
  created_at: '2026-07-09T04:15:00Z',
  created_by_member_id: OWNER.id,
  number: 1,
  owner_member_id: HANK.id,
  root_message_id: 'msg_root',
  status: 'in_progress',
  status_changed_at: '2026-07-09T04:20:00Z',
  title: '单文件番茄钟',
  workspace_id: 'ws_1',
};

const PLAN_BODY: TaskPlanBody = {
  goal: '交付一个单文件(index.html)番茄钟:25/5 相位循环、开始/暂停/重置,零依赖。',
  acceptance_criteria: [
    { id: 'AC-01', statement: 'focus 归零后自动切换到 break', verify_by: 'command', verify_ref: 'npm test' },
    { id: 'AC-02', statement: '单文件双击即可运行,无网络依赖', verify_by: 'manual', verify_ref: '' },
  ],
  defaults_decided: ['番茄时长默认 25/5'],
  out_of_scope: ['多语言 UI'],
};

// 活动版(superseded_at=null),revision=2 → 应显示 "rev 2" 徽标。
const PLAN_ACTIVE: TaskContractPublic = {
  id: 'contract_plan_active',
  body: PLAN_BODY,
  created_at: '2026-07-09T04:16:00Z',
  created_by_member_id: PAT.id,
  kind: 'task_plan',
  revision: 2,
  superseded_at: null,
  task_id: TASK.id,
  version: '1',
  workspace_id: 'ws_1',
};

// 历史版(superseded_at≠null)——不应出现在活动契约卡里,只在"历史版本"折叠区。
const PLAN_HISTORICAL: TaskContractPublic = {
  id: 'contract_plan_v1',
  body: { ...PLAN_BODY, goal: '旧版目标(已被 rev 2 取代)' },
  created_at: '2026-07-09T04:10:00Z',
  created_by_member_id: PAT.id,
  kind: 'task_plan',
  revision: 1,
  superseded_at: '2026-07-09T04:16:00Z',
  task_id: TASK.id,
  version: '1',
  workspace_id: 'ws_1',
};

const HANDOFF_BODY: TaskHandoffBody = {
  from_member: HANK.id,
  to_member: OWNER.id,
  deliverables: [{ path: 'index.html', kind: 'file' }],
  evidence: [{ type: 'test', ref: 'npm test -- --coverage', conclusion: '12/12 全绿' }],
  open_risks: ['计时漂移未压测'],
  verify_plan: '本地双击 index.html,手测三次完整循环',
};

const HANDOFF_ACTIVE: TaskContractPublic = {
  id: 'contract_handoff_active',
  body: HANDOFF_BODY,
  created_at: '2026-07-09T05:00:00Z',
  created_by_member_id: HANK.id,
  kind: 'task_handoff',
  revision: 1,
  superseded_at: null,
  task_id: TASK.id,
  version: '1',
  workspace_id: 'ws_1',
};

function renderPanel(contracts: TaskContractPublic[], detailOver: Partial<TaskDetail> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const detail: TaskDetail = { task: TASK, usage: {}, contracts, ...detailOver };
  qc.setQueryData(qk.taskDetail(TASK.id), detail);
  qc.setQueryData(qk.thread(TASK.root_message_id), []);

  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ThreadPanel
          task={TASK}
          rootMessageId={TASK.root_message_id}
          channelId={TASK.channel_id}
          memberById={MEMBER_BY_ID}
          memberNames={Object.values(MEMBER_BY_ID).map((m) => m.name)}
          meName={OWNER.name}
          meId={OWNER.id}
          presenceOf={() => undefined}
          onClose={() => {}}
          onSend={() => {}}
        />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('ThreadPanel 契约卡(M3 B-M3-1 真渲染)', () => {
  it('TaskPlan + TaskHandoff:goal/AC/deliverables/evidence 渲染,revision 徽标,历史版本折叠', () => {
    renderPanel([PLAN_HISTORICAL, PLAN_ACTIVE, HANDOFF_ACTIVE]);

    // TaskPlan:goal + AC(statement/verify_by/verify_ref)——statement 与 verify_ref 同一 .acst
    // 节点内以 " · " 相连,故用局部匹配而非整节点 exact 匹配。
    expect(screen.getByText(/交付一个单文件.*番茄钟/)).toBeInTheDocument();
    expect(screen.getByText(/focus 归零后自动切换到 break/)).toBeInTheDocument();
    expect(screen.getByText('command')).toBeInTheDocument();
    expect(screen.getByText('npm test')).toBeInTheDocument();
    expect(screen.getByText('manual')).toBeInTheDocument();

    // 活动版 revision=2 → "rev 2" 徽标可见;历史版(旧目标文案)不应出现在活动卡里。
    expect(screen.getByText('rev 2')).toBeInTheDocument();
    expect(screen.queryByText(/旧版目标/)).not.toBeInTheDocument();
    // 历史版本折叠区存在,标注条数。
    expect(screen.getByText('历史版本 ×1')).toBeInTheDocument();

    // TaskHandoff:deliverables(path+kind)、evidence(type+ref+conclusion)、verify_plan、open_risks
    expect(screen.getByText('index.html')).toBeInTheDocument();
    expect(screen.getByText('file')).toBeInTheDocument();
    expect(screen.getByText('test')).toBeInTheDocument();
    expect(screen.getByText('npm test -- --coverage')).toBeInTheDocument();
    expect(screen.getByText(/12\/12 全绿/)).toBeInTheDocument();
    expect(screen.getByText(/本地双击 index\.html/)).toBeInTheDocument();
    expect(screen.getByText(/计时漂移未压测/)).toBeInTheDocument();

    // from_member/to_member 按 memberById 解出人名,而非裸 id(而不是 mem_hank 这样的裸 id)。
    expect(screen.getByText(/Hank → Memcyo/)).toBeInTheDocument();

    // 折叠标题区摘要:TaskPlan 显 AC 条数,TaskHandoff 显 D/E 条数。
    expect(screen.getByText(/TaskPlan · AC×2/)).toBeInTheDocument();
    expect(screen.getByText(/TaskHandoff · D×1 · E×1/)).toBeInTheDocument();
  });

  it('空 contracts:占位文案存在,且不再提示"M3 接入"', () => {
    renderPanel([]);
    expect(screen.getByText(/暂无契约\(可让 @Agent 起草 TaskPlan\/TaskHandoff\)/)).toBeInTheDocument();
    expect(screen.queryByText(/M3 接入/)).not.toBeInTheDocument();
    expect(screen.queryByText(/M3/)).not.toBeInTheDocument();
  });

  it('"让 @Agent 起草"入口:列出 memberById 里 kind=agent 的候选 × TaskPlan/TaskHandoff', () => {
    renderPanel([]);
    const draftBtn = screen.getByRole('button', { name: /让 @Agent 起草/ });
    fireEvent.click(draftBtn);
    expect(screen.getByText('@Hank · TaskPlan')).toBeInTheDocument();
    expect(screen.getByText('@Hank · TaskHandoff')).toBeInTheDocument();
    expect(screen.getByText('@Pat · TaskPlan')).toBeInTheDocument();
    expect(screen.getByText('@Pat · TaskHandoff')).toBeInTheDocument();
    // owner(human)不该出现在起草候选里。
    expect(screen.queryByText(/@Memcyo/)).not.toBeInTheDocument();
  });

  it.each([
    ['pass', '通过'],
    ['downgrade', '降级通过'],
    ['send_back', '退回重做'],
    ['needs_human', '需要人类'],
  ] as const)('review_verdict=%s 显示结构化徽标', (verdict, label) => {
    renderPanel([{
      ...HANDOFF_ACTIVE,
      id: `handoff_${verdict}`,
      body: { ...HANDOFF_BODY, review_verdict: verdict },
    }]);
    expect(screen.getByText(label)).toHaveAttribute('data-verdict', verdict);
    if (verdict === 'needs_human') {
      expect(screen.getByRole('alert')).toHaveTextContent('需要人类介入');
    }
  });

  it('TaskDetail.worktree 渲染交付徽标与 Diff 入口', () => {
    renderPanel([], {
      worktree: {
        id: 'wt_1', workspace_id: 'ws_1', project_id: 'project_1', task_id: TASK.id,
        branch: 'coagentia/task-task_1', path: 'D:/worktrees/project_1/task_1', status: 'active',
        created_at: '2026-07-11T00:00:00Z',
      },
    });
    expect(screen.getByLabelText('交付工作树')).toHaveTextContent('coagentia/task-task_1');
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Diff/ })).toBeInTheDocument();
  });
});
