// 存为模板弹窗(B-M5-2 ①，对照设计稿 P13a b)：名称/描述/角色占位提取表(owner 去重可改名，
// 无 owner 归待认领)/包含节点勾选(默认全部 task 节点)。入口可用性(≥1 正式节点、无草稿层)由画布
// 工具栏 gating(saveTemplateGate)，此处只在打开态收字段。序列化真值 = server 读频道画布快照
// (§11.1，纪律 4/7)，前端只送 name/description + role_placeholders 覆盖 + include_node_ids 子集。
import { useMemo, useState } from 'react';
import { Check, Info } from 'lucide-react';

import type {
  CanvasNodePublic,
  MemberPublic,
  TaskPublic,
  TemplateCreate,
} from '@coagentia/contracts-ts';

import { useCreateTemplate } from '../data/queries';
import { Avatar } from './Avatar';
import {
  buildRolePlaceholders,
  extractRolePlaceholders,
  formalTaskNodes,
  type RolePlaceholderRow,
} from '../lib/templates';
import './templates.css';

export function SaveTemplateModal({ channelId, nodes, tasks, members, onClose }: {
  channelId: string;
  nodes: CanvasNodePublic[];
  tasks: TaskPublic[];
  members: MemberPublic[];
  onClose: () => void;
}) {
  const createM = useCreateTemplate();

  const taskById = useMemo(() => Object.fromEntries(tasks.map((t) => [t.id, t])), [tasks]);
  const memberById = useMemo(() => Object.fromEntries(members.map((m) => [m.id, m])), [members]);
  const taskNodes = useMemo(() => formalTaskNodes(nodes), [nodes]);

  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  // 角色占位提取表(可改占位名)。
  const [rows, setRows] = useState<RolePlaceholderRow[]>(
    () => extractRolePlaceholders(nodes, taskById, memberById),
  );
  // 包含节点:默认全选;取消勾选 → include_node_ids 子集,全选 → 省字段(null=全部)。
  const [included, setIncluded] = useState<Set<string>>(
    () => new Set(taskNodes.map((n) => n.id)),
  );

  const setPlaceholder = (i: number, v: string) =>
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, placeholder: v } : r)));
  const toggleNode = (id: string) =>
    setIncluded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const valid = name.trim() !== '' && included.size > 0;

  const submit = () => {
    if (!valid) return;
    const allSelected = included.size === taskNodes.length;
    const body: TemplateCreate = {
      channel_id: channelId,
      name: name.trim(),
      ...(desc.trim() ? { description: desc.trim() } : {}),
      ...(buildRolePlaceholders(rows) ? { role_placeholders: buildRolePlaceholders(rows) } : {}),
      // 全选 = 省字段(server 默认全部 task 节点);子集才显式列 id。
      ...(allSelected ? {} : { include_node_ids: taskNodes.filter((n) => included.has(n.id)).map((n) => n.id) }),
    };
    createM.mutate(body, { onSuccess: () => onClose() });
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal tmpl-modal" onClick={(e) => e.stopPropagation()} data-testid="save-template">
        <div className="mtitle">存为模板</div>

        <div className="field">
          <span className="lb">模板名称 *</span>
          <div className="inp">
            <input
              className="val"
              value={name}
              aria-label="模板名称"
              placeholder="如 工程三角(契约·实现·评审)"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <span className="lb">描述</span>
          <textarea
            className="tmpl-ta"
            rows={2}
            value={desc}
            aria-label="模板描述"
            placeholder="这个流程做什么、几步、含哪些系统节点…"
            onChange={(e) => setDesc(e.target.value)}
          />
        </div>

        <div className="field">
          <span className="lb">角色占位提取表</span>
          {rows.length === 0 ? (
            <div className="tmpl-note"><Info />画布无带 owner 的正式节点，占位表为空(实例化时全归待认领)。</div>
          ) : (
            <div className="roletable">
              {rows.map((r, i) => (
                <div className="rr" key={r.ownerId ?? '__unowned__'} data-testid="role-row">
                  <span className="owner">
                    {r.ownerId
                      ? <Avatar name={r.ownerName} size="nav" />
                      : null}
                    <span className="nm">{r.ownerId ? `@${r.ownerName}` : '待认领'}</span>
                  </span>
                  <span className="ar">→</span>
                  <span className="ph-inp">
                    <input
                      value={r.placeholder}
                      aria-label={`占位名 ${r.ownerName}`}
                      disabled={!r.ownerId}
                      onChange={(e) => setPlaceholder(i, e.target.value)}
                    />
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="field">
          <span className="lb">包含节点</span>
          <div className="tmpl-nodes">
            {taskNodes.map((n) => {
              const task = n.task_id ? taskById[n.task_id] : undefined;
              const label = task?.title ?? '(未命名任务)';
              const on = included.has(n.id);
              return (
                <label className="chkrow" key={n.id}>
                  <span
                    className={`box${on ? ' on' : ''}`}
                    role="checkbox"
                    aria-checked={on}
                    aria-label={label}
                    tabIndex={0}
                    onClick={() => toggleNode(n.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleNode(n.id); } }}
                  ><Check /></span>
                  <span>{label}</span>
                </label>
              );
            })}
            {taskNodes.length === 0 && <div className="tmpl-emptylist">画布无正式节点</div>}
          </div>
        </div>

        <div className="ops">
          <button className="btn btn-ghost" onClick={onClose}>取消</button>
          <button
            className="btn btn-primary"
            data-testid="save-template-submit"
            disabled={!valid || createM.isPending}
            onClick={submit}
          >保存</button>
        </div>
      </div>
    </div>
  );
}
