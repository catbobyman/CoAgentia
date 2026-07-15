// P4 频道文件页签(对照 P4-files.html):类型过滤 chip + mono 表头文件表 + 右侧 420px inline 预览面板。
// inline 预览类型分流(FR-4.8):图片直显 / PDF iframe / 文本·MD·CSV 取内容渲染 / 未知给元信息 + 下载。
import { useEffect, useMemo, useState } from 'react';
import { Download, Locate, X } from 'lucide-react';

import type { FilePublic } from '@coagentia/contracts-ts';

import { useChannelFiles } from '../data/queries';
import { type Cat, catIcon, categoryOf, contentUrl, fmtSize, isTextPreview } from '../lib/fileKind';
import { fmtTime } from '../lib/time';
import './files-tab.css';

type Filter = 'all' | Cat;

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'image', label: '图片' },
  { key: 'doc', label: '文档' },
  { key: 'code', label: '代码' },
  { key: 'other', label: '其他' },
];

function PreviewBody({ file }: { file: FilePublic }) {
  const cat = categoryOf(file);
  const url = contentUrl(file.id);
  const text = isTextPreview(file);
  const [body, setBody] = useState<string | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    if (!text) return;
    let alive = true;
    setBody(null);
    setErr(false);
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => { if (alive) setBody(t); })
      .catch(() => { if (alive) setErr(true); });
    return () => { alive = false; };
  }, [url, text]);

  if (cat === 'image') {
    return <img className="pimg" src={url} alt={file.name} />;
  }
  if (file.mime === 'application/pdf') {
    return <iframe className="ppdf" src={url} title={file.name} />;
  }
  if (text) {
    if (err) return <div className="pnote">预览加载失败,请下载查看。</div>;
    if (body === null) return <div className="pnote">加载中…</div>;
    return <pre className="ptext">{body}</pre>;
  }
  return <div className="pnote">该类型不支持内联预览,请下载查看。</div>;
}

export function FilesTab({ channelId, onLocate }: {
  channelId: string;
  onLocate?: (messageId: string) => void;
}) {
  const filesQ = useChannelFiles(channelId);
  const [filter, setFilter] = useState<Filter>('all');
  const [selId, setSelId] = useState<string | null>(null);

  // 倒序(新→旧)。
  const files = useMemo(
    () => [...(filesQ.data ?? [])].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
    [filesQ.data],
  );
  const shown = filter === 'all' ? files : files.filter((f) => categoryOf(f) === filter);
  const selected = files.find((f) => f.id === selId);

  return (
    <div className="filestab">
      <div className="fmain">
        <div className="ffilter">
          {FILTERS.map((f) => (
            <span
              key={f.key}
              className={`fchip${filter === f.key ? ' active' : ''}`}
              onClick={() => setFilter(f.key)}
            >{f.label}</span>
          ))}
        </div>

        <div className="ftablewrap">
          {shown.length === 0 ? (
            <div className="fempty">{filesQ.isLoading ? '加载中…' : '暂无文件'}</div>
          ) : (
            <table className="ftbl">
              <thead>
                <tr>
                  <th style={{ width: '46%' }}>Name</th>
                  <th>Size</th>
                  <th>From</th>
                  <th style={{ width: 76 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((f) => {
                  const cat = categoryOf(f);
                  return (
                    <tr
                      key={f.id}
                      className={`frow${selId === f.id ? ' sel' : ''}`}
                      onClick={() => setSelId(f.id)}
                    >
                      <td><span className="fname">{catIcon(cat)}{f.name}</span></td>
                      <td className="num">{fmtSize(f.size_bytes)}</td>
                      <td className="num">{fmtTime(f.created_at)}</td>
                      <td>
                        <span className="opscell">
                          <a
                            className="icobtn"
                            href={contentUrl(f.id)}
                            download={f.name}
                            aria-label="下载"
                            onClick={(e) => e.stopPropagation()}
                          ><Download /></a>
                          {onLocate && f.message_id && (
                            <span
                              className="icobtn"
                              aria-label="定位到消息"
                              onClick={(e) => { e.stopPropagation(); onLocate(f.message_id!); }}
                            ><Locate /></span>
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {selected && (
        <aside className="fpanel" data-screen-label="文件预览面板">
          <div className="phd">
            <span className="fn">{selected.name}</span>
            <span className="meta">{selected.mime}</span>
            <span className="icobtn" aria-label="关闭" onClick={() => setSelId(null)}><X /></span>
          </div>
          <div className="pbody">
            <PreviewBody file={selected} />
            <div className="meta-rows">
              <div className="mr"><span className="lb">Size</span><span className="vl"><span className="mono">{fmtSize(selected.size_bytes)}</span></span></div>
              {selected.message_id && (
                <div className="mr"><span className="lb">From</span><span className="vl"><span className="mono">msg {selected.message_id.slice(0, 8)} · {fmtTime(selected.created_at)}</span></span></div>
              )}
              <div className="mr"><span className="lb">SHA-256</span><span className="vl"><span className="mono">{selected.sha256.slice(0, 16)}…</span></span></div>
            </div>
          </div>
          <div className="pops">
            {onLocate && selected.message_id && (
              <button className="btn btn-ghost" onClick={() => onLocate(selected.message_id!)}>定位到消息</button>
            )}
            <a className="btn btn-secondary" href={contentUrl(selected.id)} download={selected.name}>下载</a>
          </div>
        </aside>
      )}
    </div>
  );
}
