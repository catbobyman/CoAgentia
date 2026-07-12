// P2 画布页签(对照设计稿 P2a-canvas-committed / P2d-canvas-blocked)。React Flow 渲染有向图:
// 任务节点(snapshot.nodes kind=agent,与 tasks 缓存 join 取 number/title/status/owner,实时着色)、
// 系统节点(kind=system 菱形)、边(snapshot.edges,箭头 marker)。blocked 徽标由 graph.ts 派生
// (satisfied=上游 agent 任务 done / system success)。拖拽编辑均走 writeJson、无乐观更新,
// 命中的节点/边靠 canvas.* WS 反流(wsBridge)。深链 ?node= 高亮/居中,选中回写 ?node=&?task=(双向)。
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react';
import type { Connection, Edge, Node, NodeProps, NodeTypes } from '@xyflow/react';
import {
  ChevronDown, CircleAlert, Eye, GitMerge, LayoutTemplate, Lock, Play, Plus, RefreshCw,
  Rocket, Save, Terminal, Trash2, Workflow,
} from 'lucide-react';

import type {
  AcceptanceCriterion,
  CanvasDetail,
  CanvasNodePublic,
  MemberPublic,
  MessagePublic,
  NodeCreate,
  PresenceEntry,
  ProjectPublic,
  SystemAction,
  SystemNodeStatus,
  TaskPlanBody,
  TaskPublic,
  TaskStatus,
  VerifyBy,
} from '@coagentia/contracts-ts';

import type { ChannelSearch } from '../routes/search';
import { useCanvasSnapshot, useProjects, useRetryCanvasNode } from '../data/queries';
import { deriveCanvasBlocked, wouldCreateCycle } from '../lib/graph';
import { saveTemplateGate, formalTaskNodes } from '../lib/templates';
import { STATUS_VAR, STATUS_WORD } from '../lib/uiMaps';
import { api, ApiError } from '../api';
import { Avatar } from './Avatar';
import { ForceStartModal } from './ForceStartModal';
import { SaveTemplateModal } from './SaveTemplateModal';
import { TemplateWizard } from './TemplateWizard';
import { useToast } from './Toast';
import './templates.css';
import '@xyflow/react/dist/style.css';
import './canvas-tab.css';

// 系统节点状态→色变量(idle/running/success/failed);任务节点状态色复用 uiMaps.STATUS_VAR。
const SYS_STATUS_VAR: Record<SystemNodeStatus, string> = {
  idle: '--text-muted', running: '--accent', success: '--success', failed: '--danger',
};

// ---- 节点数据形状(object type,可赋给 RF Node.data 的 Record<string, unknown> 约束)
export type TaskNodeData = {
  kind: 'agent';
  number: number;
  title: string;
  status: TaskStatus;
  ownerName?: string;
  ownerPresence?: PresenceEntry;
  activity?: string;
  blocked: boolean;
  selected: boolean;
};
export type SystemNodeData = {
  kind: 'system';
  action: SystemAction;
  status: SystemNodeStatus;
  title: string;
  blocked: boolean;
  selected: boolean;
};

// ---- 纯展示卡(不含 Handle,便于脱离 ReactFlow context 单测渲染)
export function TaskNodeCard({ data: d }: { data: TaskNodeData }) {
  return (
    <div
      className={`tnode${d.selected ? ' sel' : ''}${d.blocked ? ' blocked' : ''}`}
      style={{ '--st': `var(${STATUS_VAR[d.status]})` } as unknown as CSSProperties}
      data-testid="canvas-tnode"
    >
      <div className="hd">
        <span className="no">#{d.number}</span>
        <span className="ti">{d.title}</span>
      </div>
      <div className="mt">
        {d.ownerName && <Avatar name={d.ownerName} presence={d.ownerPresence} size="nav" />}
        <span className="dot" />
        <span className="sw">{STATUS_WORD[d.status]}</span>
      </div>
      {d.activity && <div className="act">{d.activity}</div>}
      {d.blocked && (
        <div className="blk" data-testid="node-blocked">
          <Lock />
          <span>blocked · 等待上游</span>
        </div>
      )}
    </div>
  );
}

export function SystemNodeCard({ data: d, onRetry, onShowOutput, busy = false }: {
  data: SystemNodeData;
  onRetry?: () => void;
  onShowOutput?: () => void;
  busy?: boolean;
}) {
  const Icon = d.action === 'merge' ? GitMerge : Terminal;
  const canShowOutput = d.action === 'check' && (d.status === 'success' || d.status === 'failed');
  return (
    <div
      className={`snode${d.selected ? ' sel' : ''}${d.blocked ? ' blocked' : ''}`}
      style={{ '--st': `var(${SYS_STATUS_VAR[d.status]})` } as unknown as CSSProperties}
      data-status={d.status}
      data-testid="canvas-snode"
    >
      <div className="srow">
        <span className="diamond"><Icon /></span>
        <div className="sbody">
          <span className="sttl">{d.title}</span>
          <span className="sst">{d.status}</span>
        </div>
      </div>
      {(d.status === 'failed' || canShowOutput) && (
        <div className="sactions nodrag nowheel" onClick={(e) => e.stopPropagation()}>
          {d.status === 'failed' && onRetry && (
            <button type="button" disabled={busy} aria-label={`重试 ${d.title}`} title="重试失败节点" onClick={onRetry}><RefreshCw /></button>
          )}
          {canShowOutput && onShowOutput && (
            <button type="button" aria-label={`查看 ${d.title} 输出`} title="查看输出尾" onClick={onShowOutput}><Eye /></button>
          )}
        </div>
      )}
      {d.blocked && (
        <div className="blk" data-testid="node-blocked">
          <Lock />
          <span>blocked · 等待上游</span>
        </div>
      )}
    </div>
  );
}

const SystemActionsContext = createContext<{
  retry: (nodeId: string) => void;
  showOutput: (nodeId: string) => void;
  retrying: boolean;
} | null>(null);

// ---- RF 自定义节点(卡外挂左入/右出 Handle;Handle 需 ReactFlow context)
function TaskNodeView({ data }: NodeProps) {
  return (
    <div className="rf-node">
      <Handle type="target" position={Position.Left} />
      <TaskNodeCard data={data as unknown as TaskNodeData} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
function SystemNodeView({ data, id }: NodeProps) {
  const actions = useContext(SystemActionsContext);
  const d = data as unknown as SystemNodeData;
  return (
    <div className="rf-node">
      <Handle type="target" position={Position.Left} />
      <SystemNodeCard
        data={d}
        onRetry={d.status === 'failed' && actions ? () => actions.retry(id) : undefined}
        onShowOutput={d.action === 'check' && (d.status === 'success' || d.status === 'failed') && actions
          ? () => actions.showOutput(id)
          : undefined}
        busy={actions?.retrying}
      />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes: NodeTypes = { task: TaskNodeView, system: SystemNodeView };

function systemTitle(n: CanvasNodePublic): string {
  if (n.system_action === 'merge') return 'Merge';
  return n.command ? `Check · ${n.command}` : 'Check';
}

// ---- 从快照 + tasks 缓存构建 RF 图模型（纯函数,可单测:节点 join、blocked 派生、边着色）。
export function buildCanvasModel(
  detail: CanvasDetail | undefined,
  taskById: Record<string, TaskPublic>,
  memberById: Record<string, MemberPublic>,
  presenceById: Record<string, PresenceEntry>,
  selectedNodeId: string | undefined,
): { rfNodes: Node[]; rfEdges: Edge[]; blocked: Set<string> } {
  const nodes = detail?.nodes ?? [];
  const edges = detail?.edges ?? [];
  // satisfied(上游完成:agent 任务 done / system success)+ blocked 派生走 lib/graph 单源
  // (纪律 8,与看板 blockedTaskIdsFromCanvas 同一处组装,避免规则漂移)。
  const { satisfied, blocked } = deriveCanvasBlocked(nodes, edges, taskById);

  const rfNodes: Node[] = nodes.map((n) => {
    const position = { x: n.pos_x ?? 0, y: n.pos_y ?? 0 };
    const isBlocked = blocked.has(n.id);
    const isSel = selectedNodeId === n.id;
    if (n.kind === 'system') {
      const data: SystemNodeData = {
        kind: 'system',
        action: n.system_action ?? 'check',
        status: n.system_status ?? 'idle',
        title: systemTitle(n),
        blocked: isBlocked,
        selected: isSel,
      };
      return { id: n.id, type: 'system', position, data };
    }
    const task = n.task_id ? taskById[n.task_id] : undefined;
    const owner = task?.owner_member_id ? memberById[task.owner_member_id] : undefined;
    const presence = owner ? presenceById[owner.id] : undefined;
    const data: TaskNodeData = {
      kind: 'agent',
      number: task?.number ?? 0,
      title: task?.title ?? '(未命名任务)',
      status: (task?.status ?? 'todo') as TaskStatus,
      ownerName: owner?.name,
      ownerPresence: presence,
      activity: presence?.status === 'busy' ? presence.busy_detail ?? undefined : undefined,
      blocked: isBlocked,
      selected: isSel,
    };
    return { id: n.id, type: 'task', position, data };
  });

  const rfEdges: Edge[] = edges.map((e) => {
    const srcSat = satisfied.has(e.from_node_id);
    const tgtBlocked = blocked.has(e.to_node_id);
    // 正常 border-strong;上游已完成(数据流动)accent;blocked 依赖边 warning。
    const stroke = srcSat
      ? 'var(--accent)'
      : tgtBlocked
        ? 'var(--warning)'
        : 'var(--border-strong)';
    return {
      id: e.id,
      source: e.from_node_id,
      target: e.to_node_id,
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 16, height: 16 },
      style: { stroke, strokeWidth: 1.5 },
    };
  });

  return { rfNodes, rfEdges, blocked };
}

// ---- 连边预判(纯函数,可单测):缺端点→incomplete;成环→cycle;否则 ok。
export type EdgePlan =
  | { ok: true; from: string; to: string }
  | { ok: false; reason: 'incomplete' | 'cycle' };
export function planEdgeConnect(
  edges: Array<[string, string]>,
  source: string | null | undefined,
  target: string | null | undefined,
): EdgePlan {
  if (!source || !target) return { ok: false, reason: 'incomplete' };
  if (wouldCreateCycle(edges, source, target)) return { ok: false, reason: 'cycle' };
  return { ok: true, from: source, to: target };
}

export interface CanvasTabProps {
  channelId: string;
  tasks: TaskPublic[];
  members: MemberPublic[];
  presence: PresenceEntry[];
  messages?: MessagePublic[];
  search: ChannelSearch;
  setSearch: (next: Partial<ChannelSearch>) => void;
}

// ReactFlowProvider 提供 useReactFlow(深链居中)所需 store。
export function CanvasTab(props: CanvasTabProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function CanvasInner({ channelId, tasks, members, presence, messages = [], search, setSearch }: CanvasTabProps) {
  const toast = useToast();
  const rf = useReactFlow();
  const canvasQ = useCanvasSnapshot(channelId);
  const projectsQ = useProjects();
  const retryM = useRetryCanvasNode(channelId);
  const detail = canvasQ.data;
  const canvasId = detail?.canvas?.id;

  // 稳定引用(query data 未变则 map 不重建)→ 图模型不因无关重渲染而重置拖拽位置。
  const taskById = useMemo(() => Object.fromEntries(tasks.map((t) => [t.id, t])), [tasks]);
  const memberById = useMemo(() => Object.fromEntries(members.map((m) => [m.id, m])), [members]);
  const presenceById = useMemo(
    () => Object.fromEntries(presence.map((p) => [p.member_id, p])),
    [presence],
  );
  const nodeById = useMemo(
    () => Object.fromEntries((detail?.nodes ?? []).map((n) => [n.id, n])),
    [detail],
  );

  // 选中节点 = 深链 ?node= 优先;否则由 ?task= 反查对应 agent 节点(node↔task 双向)。
  const selectedNodeId = useMemo(() => {
    if (search.node) return search.node;
    if (search.task) return (detail?.nodes ?? []).find((n) => n.task_id === search.task)?.id;
    return undefined;
  }, [search.node, search.task, detail]);

  const model = useMemo(
    () => buildCanvasModel(detail, taskById, memberById, presenceById, selectedNodeId),
    [detail, taskById, memberById, presenceById, selectedNodeId],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(model.rfNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(model.rfEdges);
  // 拖拽进行中标志(onNodeDragStart 置真 / onNodeDragStop 置假):防止模型重算(tasks/presence/
  // selection 的 WS 更新都会触发 model 重建)时把用户正拖动的节点 snap 回服务端持久化坐标、打断手势。
  const draggingRef = useRef(false);
  // 服务端为事实源:模型变化(WS 反流/join 变更)时重灌本地 RF 状态。拖拽进行中只刷新展示字段
  // (着色/标题/blocked/selected 照更)、保留本地坐标;非拖拽时全量重灌(实时着色 + 远端布局照进)。
  useEffect(() => {
    setEdges(model.rfEdges);
    if (draggingRef.current) {
      setNodes((cur) => {
        const posById = new Map(cur.map((n) => [n.id, n.position]));
        // 以 model 为成员/数据事实源(新增删除节点照常反映),仅对已在本地的节点保留其当前坐标。
        return model.rfNodes.map((n) => {
          const pos = posById.get(n.id);
          return pos ? { ...n, position: pos } : n;
        });
      });
    } else {
      setNodes(model.rfNodes);
    }
  }, [model, setNodes, setEdges]);

  // 深链居中:?node= 命中时把该节点移到视口中心(半宽 105 / 半高 44 估算)。
  useEffect(() => {
    if (!search.node) return;
    const n = model.rfNodes.find((x) => x.id === search.node);
    if (!n) return;
    rf.setCenter(n.position.x + 105, n.position.y + 44, { zoom: 1, duration: 400 });
  }, [search.node, model.rfNodes, rf]);

  const layoutTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (layoutTimer.current) clearTimeout(layoutTimer.current); }, []);

  const onCanvasError = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.code === 'GRAPH_CYCLE') {
      toast.push('连边被拒:图中会形成环(GRAPH_CYCLE)', { tone: 'error' });
    } else if (e instanceof ApiError && e.code === 'DAEMON_OFFLINE') {
      toast.push('守护进程离线,操作未生效', { tone: 'error' });
    } else if (e instanceof ApiError) {
      toast.push(e.message, { tone: 'error' });
    } else {
      toast.push('画布操作失败', { tone: 'error' });
    }
  }, [toast]);

  const [cycleWarn, setCycleWarn] = useState(false);
  const onConnect = useCallback(
    (conn: Connection) => {
      if (!canvasId) return;
      const pairs: Array<[string, string]> = edges.map((e) => [e.source, e.target]);
      const plan = planEdgeConnect(pairs, conn.source, conn.target);
      if (!plan.ok) {
        if (plan.reason === 'cycle') {
          // 成环:前端红色反馈 + toast,不发请求(server GRAPH_CYCLE 仍作兜底)。
          setCycleWarn(true);
          window.setTimeout(() => setCycleWarn(false), 1200);
          toast.push('连边会形成环,已阻止', { tone: 'error' });
        }
        return;
      }
      void api
        .createCanvasEdge(canvasId, { from_node_id: plan.from, to_node_id: plan.to })
        .catch(onCanvasError);
    },
    [canvasId, edges, toast, onCanvasError],
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      if (!canvasId) return;
      for (const e of deleted) void api.deleteCanvasEdge(canvasId, e.id).catch(onCanvasError);
    },
    [canvasId, onCanvasError],
  );

  const onNodeDragStart = useCallback(() => {
    // 拖拽开始:标记进行中,reload effect 期间保留本地坐标(不 snap 回服务端)。
    draggingRef.current = true;
  }, []);

  const onNodeDragStop = useCallback(() => {
    draggingRef.current = false;
    if (!canvasId) return;
    // 拖拽落定即捕获全量坐标进防抖 PUT(不推进基线)。
    const positions = nodes.map((n) => ({ node_id: n.id, x: n.position.x, y: n.position.y }));
    if (layoutTimer.current) clearTimeout(layoutTimer.current);
    layoutTimer.current = setTimeout(() => {
      void api.putCanvasLayout(canvasId, { positions }).catch(onCanvasError);
    }, 400);
  }, [canvasId, nodes, onCanvasError]);

  const [newOpen, setNewOpen] = useState(false);
  const [systemOpen, setSystemOpen] = useState(false);
  const [outputText, setOutputText] = useState<string | null>(null);
  // 模板▾ 下拉 + 存为模板弹窗 + 向导(B-M5-2)。
  const [tmplMenuOpen, setTmplMenuOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  // 存为模板入口 gating(§11.1 #2):≥1 正式(task)节点、无草稿层。MVP 草稿层不在快照面(proposals
  // 单独存)——恒 false 兜底,判定单点走 lib/templates.saveTemplateGate。
  const formalCount = formalTaskNodes(detail?.nodes ?? []).length;
  const saveGate = saveTemplateGate(formalCount, false);

  // force-start:选中一个 blocked 的 agent 任务节点 → 顶栏「强制启动」点亮,点开二次确认弹层。
  // (节点卡本身是脱 RF context 的纯展示卡,故把动作收在顶栏针对「当前选中节点」,而非卡内按钮。)
  const [forceTask, setForceTask] = useState<TaskPublic | null>(null);
  const selNode = selectedNodeId ? nodeById[selectedNodeId] : undefined;
  const selTask = selNode?.task_id ? taskById[selNode.task_id] : undefined;
  const canForceStart = !!selTask && !!selectedNodeId && model.blocked.has(selectedNodeId);
  const boundProjects = (projectsQ.data ?? []).filter((p) => p.channel_ids.includes(channelId));
  const showSystemOutput = useCallback((nodeId: string) => {
    const hit = [...messages].reverse().find((m) =>
      m.kind === 'system' && m.body.includes(`node_id: ${nodeId}`),
    );
    if (!hit) {
      toast.push('该节点还没有可查看的输出', { tone: 'error' });
      return;
    }
    setOutputText(hit.body);
  }, [messages, toast]);
  const systemActions = useMemo(() => ({
    retry: (nodeId: string) => retryM.mutate(nodeId),
    showOutput: showSystemOutput,
    retrying: retryM.isPending,
  }), [retryM, showSystemOutput]);

  if (canvasQ.isLoading) {
    return <section className="canvastab"><div className="boot">画布加载中…</div></section>;
  }
  if (!detail) {
    return <section className="canvastab"><div className="boot">该频道暂无画布。</div></section>;
  }

  return (
    <section
      className={`canvastab${cycleWarn ? ' cycle-warn' : ''}`}
      data-screen-label="P2 画布"
    >
      <div className="canvasbar">
        <button className="btn btn-secondary" onClick={() => setNewOpen(true)} disabled={!canvasId}>
          <Plus /> 新建 L2 任务
        </button>
        <button className="btn btn-ghost" onClick={() => setSystemOpen(true)} disabled={!canvasId}>
          <Workflow /> 新建系统节点
        </button>
        {/* force-start:仅当选中一个 blocked 的任务节点时点亮 → 二次确认弹层 → POST force-start。 */}
        <button
          className="btn btn-ghost"
          data-testid="force-start"
          disabled={!canForceStart}
          title={canForceStart
            ? '强制启动选中的 blocked 任务(越过 gating,留痕)'
            : '选中一个 blocked 任务节点以强制启动'}
          onClick={() => { if (selTask) setForceTask(selTask); }}
        >
          <Play /> 强制启动
        </button>
        {/* 模板▾:存为模板(gating)/ 从模板新建(向导)。 */}
        <div className="tmpl-menu">
          <button
            className="btn btn-ghost"
            data-testid="tmpl-menu-btn"
            aria-haspopup="menu"
            aria-expanded={tmplMenuOpen}
            onClick={() => setTmplMenuOpen((v) => !v)}
          >
            <LayoutTemplate /> 模板 <ChevronDown />
          </button>
          {tmplMenuOpen && (
            <>
              <button
                className="tmpl-backdrop"
                aria-label="关闭模板菜单"
                onClick={() => setTmplMenuOpen(false)}
              />
              <div className="tmpl-pop" role="menu">
                <span className="tipwrap">
                  <button
                    className="tmpl-item"
                    role="menuitem"
                    data-testid="save-template-item"
                    disabled={!saveGate.enabled}
                    onClick={() => { setTmplMenuOpen(false); setSaveOpen(true); }}
                  >
                    <Save /> 存为模板
                  </button>
                  {!saveGate.enabled && <span className="tip">{saveGate.hint}</span>}
                </span>
                <button
                  className="tmpl-item"
                  role="menuitem"
                  data-testid="open-wizard-item"
                  onClick={() => { setTmplMenuOpen(false); setWizardOpen(true); }}
                >
                  <Rocket /> 从模板新建…
                </button>
              </div>
            </>
          )}
        </div>
        {cycleWarn && (
          <span className="cyclemsg" role="alert"><CircleAlert /> 连边会形成环</span>
        )}
      </div>

      <div className="canvas-flow">
        <SystemActionsContext.Provider value={systemActions}>
          <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onEdgesDelete={onEdgesDelete}
          onNodeDragStart={onNodeDragStart}
          onNodeClick={(_, node) => {
            const cn = nodeById[node.id];
            setSearch({ node: node.id, task: cn?.task_id ?? undefined });
          }}
          onPaneClick={() => setSearch({ node: undefined })}
          onNodeDragStop={onNodeDragStop}
          fitView
          proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </SystemActionsContext.Provider>
        {(detail.nodes ?? []).length === 0 && (
          <div className="canvas-empty">空画布 · 从上方「新建 L2 任务」起一个节点</div>
        )}
      </div>

      {newOpen && canvasId && (
        <NewNodeModal canvasId={canvasId} projects={boundProjects} onClose={() => setNewOpen(false)} onError={onCanvasError} />
      )}
      {systemOpen && canvasId && (
        <SystemNodeModal canvasId={canvasId} onClose={() => setSystemOpen(false)} onError={onCanvasError} />
      )}
      {outputText && <SystemOutputModal body={outputText} onClose={() => setOutputText(null)} />}
      {forceTask && <ForceStartModal task={forceTask} onClose={() => setForceTask(null)} />}
      {saveOpen && (
        <SaveTemplateModal
          channelId={channelId}
          nodes={detail.nodes ?? []}
          tasks={tasks}
          members={members}
          onClose={() => setSaveOpen(false)}
        />
      )}
      {wizardOpen && (
        <TemplateWizard
          channelId={channelId}
          members={members}
          onClose={() => setWizardOpen(false)}
          // 目标 = 当前频道画布(已在此)：落地广播 + invalidate 由 mutation 完成，关窗即可。
          onInstantiated={() => setWizardOpen(false)}
        />
      )}
    </section>
  );
}

// 新建 L2 任务节点弹层 = 「升格补契约」的落地面(手填 TaskPlan 路)。画布经 POST nodes 建的任务
// 即 L2(进画布=正式立项,契约 §4.3 v1 必填 goal + ≥1 AC),故此处就是补契约框:goal、多条
// 验收标准(可增删,每条 statement + verify_by + verify_ref)、可选 defaults_decided / out_of_scope。
// 「让 @Agent 起草」一路针对既有任务(ThreadPanel 的 request-draft 下拉),此处无 task_id 故不适用;
// 既有 L1 任务升格走任务线程面板 PATCH level=l2(无「引用既有任务为节点」端点)。
type AcDraft = { statement: string; verify_by: VerifyBy; verify_ref: string };

// 多行文本 → 去空白后的非空行数组(defaults_decided / out_of_scope 的输入形状)。
function splitLines(s: string): string[] {
  return s.split('\n').map((x) => x.trim()).filter((x) => x !== '');
}

export function NewNodeModal({ canvasId, projects = [], onClose, onError }: {
  canvasId: string;
  projects?: ProjectPublic[];
  onClose: () => void;
  onError: (e: unknown) => void;
}) {
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [acs, setAcs] = useState<AcDraft[]>([{ statement: '', verify_by: 'manual', verify_ref: '' }]);
  const [defaults, setDefaults] = useState('');
  const [oos, setOos] = useState('');
  const [writesCode, setWritesCode] = useState(false);
  const [projectId, setProjectId] = useState('');
  const [busy, setBusy] = useState(false);

  const filledAcs = acs.filter((a) => a.statement.trim() !== '');
  const valid = title.trim() !== '' && goal.trim() !== '' && filledAcs.length > 0
    && (!writesCode || projectId !== '');

  const patchAc = (i: number, patch: Partial<AcDraft>) =>
    setAcs((prev) => prev.map((a, idx) => (idx === i ? { ...a, ...patch } : a)));
  const addAc = () => setAcs((prev) => [...prev, { statement: '', verify_by: 'manual', verify_ref: '' }]);
  const removeAc = (i: number) => setAcs((prev) => (prev.length <= 1 ? prev : prev.filter((_, idx) => idx !== i)));

  const submit = async () => {
    if (!valid) return;
    setBusy(true);
    // 只提交有 statement 的行;id 顺序稳定 ac-1..n。非空 tuple 由 valid 保证(filledAcs.length>0)。
    const criteria = filledAcs.map((a, i): AcceptanceCriterion => ({
      id: `ac-${i + 1}`,
      statement: a.statement.trim(),
      verify_by: a.verify_by,
      verify_ref: a.verify_ref.trim(),
    }));
    const defaultsArr = splitLines(defaults);
    const oosArr = splitLines(oos);
    const plan: TaskPlanBody = {
      goal: goal.trim(),
      acceptance_criteria: criteria as [AcceptanceCriterion, ...AcceptanceCriterion[]],
      version: 'coagentia.task-plan.v1',
      ...(defaultsArr.length ? { defaults_decided: defaultsArr } : {}),
      ...(oosArr.length ? { out_of_scope: oosArr } : {}),
    };
    const body: NodeCreate = {
      kind: 'agent', title: title.trim(), task_plan: plan,
      ...(writesCode ? { writes_code: true, project_id: projectId } : {}),
    };
    try {
      await api.createCanvasNode(canvasId, body); // 成功靠 canvas.node_added WS 反流,无乐观更新
      onClose();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal nodemodal" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle">新建 L2 任务节点</div>
        <div className="nmnote">进画布即正式立项(L2),故必须补齐契约:目标 + 至少一条验收标准。</div>
        <div className="field">
          <span className="lb">标题</span>
          <div className="inp"><input className="val" value={title} placeholder="任务标题" onChange={(e) => setTitle(e.target.value)} /></div>
        </div>
        <div className="field">
          <span className="lb">目标 (goal)</span>
          <div className="inp"><input className="val" value={goal} placeholder="这个任务要达成什么" onChange={(e) => setGoal(e.target.value)} /></div>
        </div>
        <div className="code-task-row">
          <div><b>代码任务</b><span>激活后在独立 worktree 中交付</span></div>
          <button type="button" role="switch" aria-label="代码任务" aria-checked={writesCode} className={`cs-toggle${writesCode ? ' on' : ''}`} onClick={() => { setWritesCode((v) => !v); setProjectId(''); }}><span className="knob" /></button>
        </div>
        {writesCode && (
          <div className="field">
            <label className="lb" htmlFor="code-project">绑定 Project</label>
            <select id="code-project" className="csel project-pick" aria-label="绑定 Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">选择当前频道 Project</option>
              {projects.map((p) => <option value={p.id} key={p.id}>{p.name} · {p.repo_path}</option>)}
            </select>
            {projects.length === 0 && <div className="nmnote project-note">请先在频道设置绑定 Project。</div>}
          </div>
        )}

        <div className="field">
          <div className="aclbrow">
            <span className="lb">验收标准 (acceptance criteria)</span>
            <button className="acadd" type="button" onClick={addAc}><Plus /> 增加一条</button>
          </div>
          <div className="acedit">
            {acs.map((a, i) => (
              <div className="acitem" key={i} data-testid="ac-row">
                <div className="inp"><input className="val" value={a.statement} placeholder={`验收判据 ${i + 1}`} onChange={(e) => patchAc(i, { statement: e.target.value })} /></div>
                <div className="cvby">
                  <select className="csel" value={a.verify_by} onChange={(e) => patchAc(i, { verify_by: e.target.value as VerifyBy })}>
                    <option value="manual">manual</option>
                    <option value="command">command</option>
                    <option value="inspect">inspect</option>
                  </select>
                  <div className="inp"><input className="val" value={a.verify_ref} placeholder="verify_ref(命令/引用,可空)" onChange={(e) => patchAc(i, { verify_ref: e.target.value })} /></div>
                  <button
                    className="acdel"
                    type="button"
                    aria-label="删除这条验收标准"
                    disabled={acs.length <= 1}
                    onClick={() => removeAc(i)}
                  ><Trash2 /></button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="field">
          <span className="lb">已定默认值 (defaults decided,每行一条,可空)</span>
          <textarea className="nmta" rows={2} value={defaults} placeholder="例:番茄时长默认 25/5" onChange={(e) => setDefaults(e.target.value)} />
        </div>
        <div className="field">
          <span className="lb">超纲范围 (out of scope,每行一条,可空)</span>
          <textarea className="nmta" rows={2} value={oos} placeholder="例:多语言 UI" onChange={(e) => setOos(e.target.value)} />
        </div>

        <div className="ops">
          <button className="btn btn-ghost" onClick={onClose}>取消</button>
          <button className="btn btn-primary" disabled={busy || !valid} onClick={() => void submit()}>创建</button>
        </div>
      </div>
    </div>
  );
}

export function SystemNodeModal({ canvasId, onClose, onError }: {
  canvasId: string;
  onClose: () => void;
  onError: (e: unknown) => void;
}) {
  const [action, setAction] = useState<SystemAction>('merge');
  const [command, setCommand] = useState('');
  const [busy, setBusy] = useState(false);
  const valid = action === 'merge' || command.trim() !== '';
  const submit = async () => {
    if (!valid) return;
    setBusy(true);
    const body: NodeCreate = action === 'merge'
      ? { kind: 'system', title: 'Merge', system_action: 'merge' }
      : { kind: 'system', title: 'Check', system_action: 'check', command: command.trim() };
    try {
      await api.createCanvasNode(canvasId, body);
      onClose();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal system-node-modal" role="dialog" aria-label="新建系统节点" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle">新建系统节点</div>
        <div className="system-action-seg" role="radiogroup" aria-label="系统动作">
          <button type="button" role="radio" aria-checked={action === 'merge'} className={action === 'merge' ? 'active' : ''} onClick={() => setAction('merge')}><GitMerge />Merge</button>
          <button type="button" role="radio" aria-checked={action === 'check'} className={action === 'check' ? 'active' : ''} onClick={() => setAction('check')}><Terminal />Check</button>
        </div>
        {action === 'check' && (
          <div className="field">
            <label className="lb" htmlFor="check-command">Check 命令</label>
            <div className="inp"><input id="check-command" className="val mono" aria-label="Check 命令" value={command} onChange={(e) => setCommand(e.target.value)} /></div>
          </div>
        )}
        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button type="button" className="btn btn-primary" disabled={!valid || busy} onClick={() => void submit()}>创建系统节点</button>
        </div>
      </div>
    </div>
  );
}

function SystemOutputModal({ body, onClose }: { body: string; onClose: () => void }) {
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal system-output-modal" role="dialog" aria-label="系统节点输出" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle">Check 输出尾</div>
        <pre>{body}</pre>
        <div className="ops"><button type="button" className="btn btn-primary" onClick={onClose}>关闭</button></div>
      </div>
    </div>
  );
}
