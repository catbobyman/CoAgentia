// PS-WT ① 新建 Project 弹窗（侧栏「项目」区头 ＋ 落点，B §6.2）。Claude 式轻量：只收
// 名称 / Computer 下拉 / 仓库路径（文本框 +「浏览…」→ FolderPickerModal）；dev/deploy 命令等高级项
// 不进弹窗（建后去频道设置的 Project 编辑器补）。提交 = 既有 POST /projects（server 补默认保留天数/
// 预览空闲）。「浏览…」在未选 Computer 时禁用 + tooltip。VALIDATION_FAILED / NAME_TAKEN 等结构化错误
// 就地报错不关窗（同 CreateAgentModal / NewChannelModal 体例）。
import { useEffect, useState } from 'react';
import { Server } from 'lucide-react';

import type { ComputerPublic, ProjectCreate, ProjectPublic } from '@coagentia/contracts-ts';

import { ApiError } from '../api';
import { useComputers, useCreateProject } from '../data/queries';
import { RepoPathField } from './RepoPathField';
import './create-agent.css';
import './project-settings.css'; // 复用 .project-select 下拉样式

export function NewProjectModal({ onClose, onCreated }: {
  onClose: () => void;
  /** 创建成功回调（调用方据此选中/跳转）。 */
  onCreated?: (project: ProjectPublic) => void;
}) {
  const computersQ = useComputers();
  const createM = useCreateProject();
  const computers: ComputerPublic[] = computersQ.data ?? [];

  const [name, setName] = useState('');
  const [repoPath, setRepoPath] = useState('');
  const [computerId, setComputerId] = useState('');
  const [error, setError] = useState('');

  // 唯一 Computer 时预选（computers 内部异步拉取，初值渲染时可能未到 → effect 兜底，不覆盖已选）。
  useEffect(() => {
    if (computerId === '' && computers.length === 1) setComputerId(computers[0]!.id);
  }, [computers, computerId]);

  const valid = name.trim() !== '' && repoPath.trim() !== '' && computerId !== '';
  const busy = createM.isPending;

  const submit = () => {
    if (!valid || busy) return;
    setError('');
    const body: ProjectCreate = {
      name: name.trim(),
      repo_path: repoPath.trim(),
      computer_id: computerId,
    };
    createM.mutate(body, {
      // useCreateProject 复用泛型 useProjectMutation（返回窄化为 unknown）；createProject 端点形状即
      // ProjectPublic，回调按其真实形状消费。
      onSuccess: (project) => {
        onCreated?.(project as ProjectPublic);
        onClose();
      },
      onError: (e: unknown) =>
        setError(e instanceof ApiError ? e.message : '新建 Project 失败'),
    });
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal create-agent-modal"
        role="dialog"
        aria-label="新建 Project"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mtitle">新建 Project</div>

        <div className="field">
          <label className="lb" htmlFor="np-name">名称</label>
          <div className="inp">
            <input
              id="np-name" className="val" value={name} placeholder="Project 名称"
              autoFocus
              onChange={(e) => setName(e.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label className="lb" htmlFor="np-computer">Computer</label>
          <span className="project-select">
            <Server />
            <select
              id="np-computer" aria-label="Computer" value={computerId}
              onChange={(e) => setComputerId(e.target.value)}
            >
              <option value="">选择 Computer</option>
              {computers.map((c) => <option value={c.id} key={c.id}>{c.name}</option>)}
            </select>
          </span>
        </div>

        <RepoPathField value={repoPath} onChange={setRepoPath} computerId={computerId} />

        {error && <div className="ca-error" role="alert">{error}</div>}

        <div className="ops">
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button
            type="button" className="btn btn-primary"
            disabled={!valid || busy}
            onClick={submit}
          >
            创建 Project
          </button>
        </div>
      </div>
    </div>
  );
}
