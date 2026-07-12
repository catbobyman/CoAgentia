import { useMemo, useState } from 'react';
import { FolderGit2, Link2, Link2Off, Pencil, Plus, Server, Trash2 } from 'lucide-react';

import type {
  ComputerPublic,
  ProjectCreate,
  ProjectPatch,
  ProjectPublic,
} from '@coagentia/contracts-ts';

import { ApiError } from '../api';
import {
  useBindProject,
  useComputers,
  useCreateProject,
  useDeleteProject,
  usePatchProject,
  useProjects,
  useUnbindProject,
} from '../data/queries';
import './project-settings.css';

export function ProjectSettingsSection({ channelId, canManage }: {
  channelId: string;
  canManage: boolean;
}) {
  if (!canManage) return null;
  return <ManagedProjectSettings channelId={channelId} />;
}

function ManagedProjectSettings({ channelId }: { channelId: string }) {
  const projectsQ = useProjects();
  const computersQ = useComputers();
  const bindM = useBindProject();
  const unbindM = useUnbindProject();
  const deleteM = useDeleteProject();
  const [editing, setEditing] = useState<ProjectPublic | 'new' | null>(null);

  const projects = projectsQ.data ?? [];
  const bound = projects.filter((p) => p.channel_ids.includes(channelId));
  const unbound = projects.filter((p) => !p.channel_ids.includes(channelId));

  const remove = (project: ProjectPublic) => {
    if (!window.confirm(`删除 Project “${project.name}”？`)) return;
    deleteM.mutate(project.id);
  };

  return (
    <div className="cs-sec project-settings">
      <div className="cs-label"><FolderGit2 />Project</div>
      <div className="project-tools">
        <span>{bound.length} 个已绑定</span>
        <button
          type="button" className="btn btn-secondary" aria-label="新建 Project"
          disabled={computersQ.isLoading || (computersQ.data?.length ?? 0) === 0}
          title={(computersQ.data?.length ?? 0) === 0 ? '先创建并连接 Computer' : '新建 Project'}
          onClick={() => setEditing('new')}
        >
          <Plus />新建 Project
        </button>
      </div>
      <div className="cs-card">
        {projectsQ.isLoading && <div className="project-empty">加载 Project…</div>}
        {!projectsQ.isLoading && projects.length === 0 && (
          <div className="project-empty">当前工作区还没有 Project</div>
        )}
        {[...bound, ...unbound].map((project) => {
          const isBound = project.channel_ids.includes(channelId);
          const busy = bindM.isPending || unbindM.isPending || deleteM.isPending;
          return (
            <div className="project-row" key={project.id}>
              <span className={`project-bind-state${isBound ? ' on' : ''}`} title={isBound ? '已绑定当前频道' : '未绑定'}>
                <FolderGit2 />
              </span>
              <span className="project-main">
                <b>{project.name}</b>
                <span>{project.repo_path}</span>
              </span>
              <button
                type="button" className="project-icon" disabled={busy}
                aria-label={`${isBound ? '解除绑定' : '绑定'} ${project.name}`}
                title={isBound ? '解除当前频道绑定' : '绑定到当前频道'}
                onClick={() => isBound
                  ? unbindM.mutate({ channelId, projectId: project.id })
                  : bindM.mutate({ channelId, projectId: project.id })}
              >{isBound ? <Link2Off /> : <Link2 />}</button>
              <button type="button" className="project-icon" aria-label={`编辑 ${project.name}`} title="编辑 Project" onClick={() => setEditing(project)}>
                <Pencil />
              </button>
              <button type="button" className="project-icon danger" disabled={busy} aria-label={`删除 ${project.name}`} title="删除 Project" onClick={() => remove(project)}>
                <Trash2 />
              </button>
            </div>
          );
        })}
      </div>
      {editing && (
        <ProjectEditorModal
          project={editing === 'new' ? undefined : editing}
          computers={computersQ.data ?? []}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function ProjectEditorModal({ project, computers, onClose }: {
  project?: ProjectPublic;
  computers: ComputerPublic[];
  onClose: () => void;
}) {
  const createM = useCreateProject();
  const patchM = usePatchProject();
  const defaultComputer = project?.computer_id ?? (computers.length === 1 ? computers[0]!.id : '');
  const [name, setName] = useState(project?.name ?? '');
  const [repoPath, setRepoPath] = useState(project?.repo_path ?? '');
  const [computerId, setComputerId] = useState(defaultComputer);
  const [devCommand, setDevCommand] = useState(project?.dev_command ?? '');
  const [deployCommand, setDeployCommand] = useState(project?.deploy_command ?? '');
  const [keepDays, setKeepDays] = useState(String(project?.worktree_keep_days ?? 7));
  const [idleMin, setIdleMin] = useState(String(project?.preview_idle_min ?? 30));
  const [error, setError] = useState('');

  const valid = name.trim() !== '' && repoPath.trim() !== '' && computerId !== ''
    && Number(keepDays) >= 0 && Number(idleMin) >= 0;
  const busy = createM.isPending || patchM.isPending;

  const patch = useMemo((): ProjectPatch => {
    if (!project) return {};
    const next: ProjectPatch = {};
    if (name.trim() !== project.name) next.name = name.trim();
    if (repoPath.trim() !== project.repo_path) next.repo_path = repoPath.trim();
    if (computerId !== project.computer_id) next.computer_id = computerId;
    if (devCommand.trim() !== (project.dev_command ?? '')) next.dev_command = devCommand.trim() || null;
    if (deployCommand.trim() !== (project.deploy_command ?? '')) next.deploy_command = deployCommand.trim() || null;
    if (Number(keepDays) !== project.worktree_keep_days) next.worktree_keep_days = Number(keepDays);
    if (Number(idleMin) !== project.preview_idle_min) next.preview_idle_min = Number(idleMin);
    return next;
  }, [project, name, repoPath, computerId, devCommand, deployCommand, keepDays, idleMin]);

  const submit = async () => {
    if (!valid) return;
    setError('');
    try {
      if (project) {
        if (Object.keys(patch).length > 0) {
          await patchM.mutateAsync({ projectId: project.id, patch });
        }
      } else {
        const body: ProjectCreate = {
          name: name.trim(), repo_path: repoPath.trim(), computer_id: computerId,
          worktree_keep_days: Number(keepDays), preview_idle_min: Number(idleMin),
          ...(devCommand.trim() ? { dev_command: devCommand.trim() } : {}),
          ...(deployCommand.trim() ? { deploy_command: deployCommand.trim() } : {}),
        };
        await createM.mutateAsync(body);
      }
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Project 保存失败');
    }
  };

  return (
    <div className="scrim project-editor-scrim" onClick={onClose}>
      <div className="modal project-editor" role="dialog" aria-label="Project 编辑器" onClick={(e) => e.stopPropagation()}>
        <div className="mtitle">{project ? '编辑 Project' : '新建 Project'}</div>
        <ProjectInput label="Project 名称" value={name} onChange={setName} />
        <ProjectInput label="仓库路径" value={repoPath} onChange={setRepoPath} mono />
        <div className="field">
          <label className="lb" htmlFor="project-computer">Computer</label>
          <span className="project-select"><Server /><select id="project-computer" aria-label="Computer" value={computerId} onChange={(e) => setComputerId(e.target.value)}>
            <option value="">选择 Computer</option>
            {computers.map((c) => <option value={c.id} key={c.id}>{c.name}</option>)}
          </select></span>
        </div>
        <ProjectInput label="开发命令" value={devCommand} onChange={setDevCommand} mono optional />
        <ProjectInput label="部署命令" value={deployCommand} onChange={setDeployCommand} mono optional />
        <div className="project-number-grid">
          <ProjectInput label="保留天数" value={keepDays} onChange={setKeepDays} inputMode="numeric" />
          <ProjectInput label="预览空闲分钟" value={idleMin} onChange={setIdleMin} inputMode="numeric" />
        </div>
        {error && <div className="project-error" role="alert">{error}</div>}
        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button type="button" className="btn btn-primary" disabled={!valid || busy} onClick={() => void submit()}>
            {project ? '保存 Project' : '创建 Project'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ProjectInput({ label, value, onChange, mono, optional, inputMode }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  mono?: boolean;
  optional?: boolean;
  inputMode?: 'numeric';
}) {
  return (
    <div className="field">
      <label className="lb">{label}{optional ? '（可选）' : ''}</label>
      <div className="inp"><input className={`val${mono ? ' mono' : ''}`} aria-label={label} inputMode={inputMode} value={value} onChange={(e) => onChange(e.target.value)} /></div>
    </div>
  );
}
