// 终端风编辑器(设计稿 §6)。As Task 复选 → POST as_task(契约 B);回显靠 WS 广播(契约 C §5)。
// variant='panel'(P5 线程面板)使用 .pcomposer 外框且隐藏 As Task(线程回复不转任务)。
import { useState } from 'react';

export function Composer({ channelName, onSend, variant = 'main', hideAsTask = false }: {
  channelName: string;
  onSend: (body: string, asTask: boolean) => void | Promise<void>;
  variant?: 'main' | 'panel';
  hideAsTask?: boolean;
}) {
  const [draft, setDraft] = useState('');
  const [asTask, setAsTask] = useState(false);

  const send = () => {
    const body = draft.trim();
    if (!body) return;
    setDraft('');
    setAsTask(false);
    void onSend(body, asTask);
  };

  return (
    <footer className={variant === 'panel' ? 'pcomposer' : 'composer'}>
      <div className="combox">
        <span className="prompt">❯</span>
        <input
          className="line"
          value={draft}
          placeholder={`发消息到 #${channelName}`}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
        />
        {!hideAsTask && (
          <label className="astask">
            <input type="checkbox" checked={asTask} onChange={(e) => setAsTask(e.target.checked)} />
            As Task
          </label>
        )}
        <button className="btn btn-primary" onClick={send}>发送</button>
      </div>
    </footer>
  );
}
