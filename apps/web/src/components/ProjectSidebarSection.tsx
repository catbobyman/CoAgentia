// PS-WT ① 侧栏「项目」区（Claude.ai 式，置于 CHANNELS 分组上方，B §6.1）。数据 = 既有 GET /projects
// （已带 channel_ids，零新读端点）+ 频道名从既有频道缓存映射。展开 = 该项目绑定频道（第二入口/引用，
// 点击跳频道；CHANNELS 主列表原样不动，多对多频道两处都出现）。区头 ＋ = NewProjectModal；项目行 ＋ =
// 该项目下新建频道（复用 NewChannelModal，创建成功后调 POST /channels/{cid}/projects 绑定）。
// GET /projects 是 admin 门 → 仅 admin 渲染此区（canManage 由 RootLayout 据 me.role 传入）。
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, FolderGit2, Lock, Plus } from 'lucide-react';

import type { ChannelPublic } from '@coagentia/contracts-ts';

import { useBindProject, useProjects } from '../data/queries';
import { NewChannelModal } from './NewChannelModal';
import { NewProjectModal } from './NewProjectModal';
import './project-sidebar.css';

export interface ProjectSidebarSectionProps {
  channels: ChannelPublic[];
  activeChannelId: string | undefined;
  onSelectChannel: (ch: ChannelPublic) => void;
  /** GET /projects 是 admin 门 → 仅 admin 渲染此区。 */
  canManage: boolean;
}

export function ProjectSidebarSection(props: ProjectSidebarSectionProps) {
  if (!props.canManage) return null;
  return <ManagedProjectSidebar {...props} />;
}

function ManagedProjectSidebar({ channels, activeChannelId, onSelectChannel }: ProjectSidebarSectionProps) {
  const projectsQ = useProjects();
  const bindM = useBindProject();
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // 正在「项目下新建频道」的目标 project id（NewChannelModal 打开态）。
  const [newChannelFor, setNewChannelFor] = useState<string | null>(null);

  const projects = projectsQ.data ?? [];
  const channelById = useMemo(
    () => Object.fromEntries(channels.map((c) => [c.id, c])),
    [channels],
  );

  const toggle = (id: string) => setExpanded((m) => ({ ...m, [id]: !m[id] }));

  return (
    <div className="ps-section">
      <div className="ps-head">
        <span>项目</span>
        <span className="ps-head-line" />
        <button
          type="button" className="ps-add"
          aria-label="新建 Project"
          onClick={() => setNewProjectOpen(true)}
        ><Plus /></button>
      </div>

      {!projectsQ.isLoading && projects.length === 0 && (
        <div className="ps-empty">还没有 Project</div>
      )}

      {projects.map((p) => {
        const open = !!expanded[p.id];
        const boundChannels = p.channel_ids
          .map((cid) => channelById[cid])
          .filter((c): c is ChannelPublic => !!c);
        return (
          <div key={p.id}>
            <div
              className="ps-proj"
              role="button"
              aria-expanded={open}
              aria-label={`项目 ${p.name}`}
              onClick={() => toggle(p.id)}
            >
              {open ? <ChevronDown /> : <ChevronRight />}
              <FolderGit2 className="ps-proj-ic" />
              <span className="nm">{p.name}</span>
              <button
                type="button" className="ps-proj-add"
                aria-label={`在 ${p.name} 下新建频道`}
                onClick={(e) => { e.stopPropagation(); setNewChannelFor(p.id); }}
              ><Plus /></button>
            </div>
            {open && boundChannels.map((ch) => (
              <div
                key={ch.id}
                className={`ch ps-chan${ch.id === activeChannelId ? ' active' : ''}`}
                onClick={() => onSelectChannel(ch)}
              >
                {ch.is_private
                  ? <span className="lock"><Lock /></span>
                  : <span className="hash">#</span>}
                <span className="nm">{ch.name}</span>
              </div>
            ))}
            {open && boundChannels.length === 0 && (
              <div className="ps-chan-empty">还没有绑定频道</div>
            )}
          </div>
        );
      })}

      {newProjectOpen && <NewProjectModal onClose={() => setNewProjectOpen(false)} />}

      {newChannelFor && (
        <NewChannelModal
          onClose={() => setNewChannelFor(null)}
          onCreated={(ch) => {
            // 建频道自动绑定该项目：第二发失败 → useBindProject 的 onError toast，频道照常在 CHANNELS
            // 出现（无孤儿副作用）。绑定成功后 projects 失效，频道进该项目组。
            bindM.mutate({ channelId: ch.id, projectId: newChannelFor });
            setNewChannelFor(null);
          }}
        />
      )}
    </div>
  );
}
