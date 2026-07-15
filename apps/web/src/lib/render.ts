// 消息正文渲染(FR-4.3:@ 与 task #n 是纯文本渲染,非外键)。从 App.tsx 抽出,供 MessageFlow 复用。
export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function renderBody(body: string, memberNames: string[], meName: string): string {
  const parts = body.split(/```(\w*)\n([\s\S]*?)```/g);
  let html = '';
  for (let i = 0; i < parts.length; i += 3) {
    let text = escapeHtml(parts[i] ?? '').trim();
    text = text.replace(/`([^`]+)`/g, '<span class="icode">$1</span>');
    for (const name of memberNames) {
      const cls = name === meName ? 'mention self' : 'mention';
      text = text.replaceAll(`@${name}`, `<span class="${cls}">@${name}</span>`);
    }
    text = text.replace(/task #(\d+)/g, 'task <span class="tasklink">#$1</span>');
    html += text.replace(/\n/g, '<br/>');
    if (i + 2 < parts.length) {
      const lang = escapeHtml(parts[i + 1] ?? '');
      const code = escapeHtml(parts[i + 2] ?? '');
      html += `<div class="codeblock"><span class="lang">${lang}</span><pre>${code}</pre></div>`;
    }
  }
  return html;
}
