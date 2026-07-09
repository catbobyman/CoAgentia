// 终端风编辑器(设计稿 §6)。As Task 复选 → POST as_task(契约 B);回显靠 WS 广播(契约 C §5)。
import { useState } from 'react';

export function Composer({ channelName, onSend }: {
  channelName: string;
  onSend: (body: string, asTask: boolean) => void | Promise<void>;
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
    <footer className="composer">
      <div className="combox">
        <span className="prompt">❯</span>
        <input
          className="line"
          value={draft}
          placeholder={`发消息到 #${channelName}`}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
        />
        <label className="astask">
          <input type="checkbox" checked={asTask} onChange={(e) => setAsTask(e.target.checked)} />
          As Task
        </label>
        <button className="btn btn-primary" onClick={send}>发送</button>
      </div>
    </footer>
  );
}
