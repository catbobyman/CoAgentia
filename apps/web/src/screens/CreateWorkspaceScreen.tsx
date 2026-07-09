// P0b 创建工作区(独立于主壳):E7 点阵背景 + 居中 560px 卡片,name → slug 实时生成。
// POST /api/workspace(mock 恒 409 WORKSPACE_EXISTS,仅验形状)。
import { useState } from 'react';

import { api } from '../api';

function slugify(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

export function CreateWorkspaceScreen() {
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const effectiveSlug = slugTouched ? slug : slugify(name);

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await api.createWorkspace({ name: name.trim(), slug: effectiveSlug });
    } catch {
      // mock 单工作区已存在 → 409(仅验形状/失败路径)
      setError('该工作区已存在(MVP 单工作区)。');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="p0screen">
      <div className="p0center">
        <div className="p0card">
          <div className="p0title">CREATE WORKSPACE<span className="u">_</span></div>

          <div className="field">
            <span className="lb">工作区名称</span>
            <div className="inp">
              <span className="pr">❯</span>
              <input
                className="val"
                value={name}
                placeholder="Memcyo Lab"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>

          <div className="field">
            <span className="lb">slug</span>
            <div className="inp">
              <input
                className="val mono"
                value={effectiveSlug}
                placeholder="memcyo-lab"
                onChange={(e) => { setSlugTouched(true); setSlug(e.target.value); }}
              />
            </div>
            <div className="hint">由名称实时生成,可修改</div>
          </div>

          {error && <div className="p0error">{error}</div>}

          <div className="ops">
            <button
              className="btn btn-primary"
              disabled={!name.trim() || submitting}
              onClick={() => void submit()}
            >创建工作区</button>
          </div>
        </div>
      </div>
    </div>
  );
}
