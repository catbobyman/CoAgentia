// PS-WT ① 仓库路径字段（共用件，双入口：NewProjectModal + ProjectSettingsSection 编辑器）。
// 文本框（保持可手改，网络盘等全盘浏览覆盖不到的场景兜底）+「浏览…」按钮 → FolderPickerModal。
// 未选 Computer 时「浏览…」禁用 + tooltip（浏览必须走该机 daemon 查询代理）。
import { useState } from 'react';
import { FolderSearch } from 'lucide-react';

import { FolderPickerModal } from './FolderPickerModal';
import './repo-path-field.css';

export function RepoPathField({ label = '仓库路径', value, onChange, computerId }: {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  /** 选中的 Computer；空 = 未选，浏览禁用。 */
  computerId: string;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const canBrowse = computerId !== '';

  return (
    <div className="field">
      <label className="lb">{label}</label>
      <div className="repo-path-row">
        <div className="inp">
          <input
            className="val mono"
            aria-label={label}
            value={value}
            placeholder="仓库绝对路径"
            onChange={(e) => onChange(e.target.value)}
          />
        </div>
        <span className="tipwrap">
          <button
            type="button"
            className="btn btn-secondary repo-browse-btn"
            disabled={!canBrowse}
            aria-label="浏览"
            onClick={() => setPickerOpen(true)}
          >
            <FolderSearch />浏览…
          </button>
          {!canBrowse && <span className="tip">先选择 Computer</span>}
        </span>
      </div>
      {pickerOpen && canBrowse && (
        <FolderPickerModal
          computerId={computerId}
          onPick={(p) => onChange(p)}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
