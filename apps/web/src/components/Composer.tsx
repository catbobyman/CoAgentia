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

  // forceTask: Ctrl/Cmd+Shift+Enter 快捷键旁路复选,强制以 as_task 提交(线程面板 hideAsTask 时不生效)。
  const send = (forceTask = false) => {
    const body = draft.trim();
    if (!body) return;
    setDraft('');
    setAsTask(false);
    void onSend(body, forceTask || asTask);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    // 输入法组合态的 Enter 是"确认候选词"不是"发送"(全中文主路径;keyCode 229 兼容旧 IME)。
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    // Ctrl/Cmd+Shift+Enter = 直接转任务;其余任意 Enter 组合一律照发(M1 语义,不静默吞键)。
    send((e.ctrlKey || e.metaKey) && e.shiftKey && !hideAsTask);
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
          onKeyDown={onKeyDown}
        />
        {!hideAsTask && (
          <label className="astask">
            <input type="checkbox" checked={asTask} onChange={(e) => setAsTask(e.target.checked)} />
            As Task
          </label>
        )}
        <button className="btn btn-primary" onClick={() => send()}>发送</button>
      </div>
    </footer>
  );
}
