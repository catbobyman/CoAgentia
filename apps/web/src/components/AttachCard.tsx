// 消息流附件卡(FR-4.8):文件名 / 类型 / 大小 + 预览(新标签打开内容)/ 下载。
// 数据源 = 频道文件按 message_id 聚合(ChannelChatScreen 组装,MessageFlow 逐条消息渲染)。
import { Download } from 'lucide-react';

import type { FilePublic } from '@coagentia/contracts-ts';

import { catIcon, categoryOf, contentUrl, fmtSize } from '../lib/fileKind';
import './attach-card.css';

export function AttachCard({ file }: { file: FilePublic }) {
  const url = contentUrl(file.id);
  return (
    <div className="attach">
      <span className="fic">{catIcon(categoryOf(file))}</span>
      <a className="fn" href={url} target="_blank" rel="noreferrer" title="预览">{file.name}</a>
      <span className="sz">{fmtSize(file.size_bytes)}</span>
      <a className="dl" href={url} download={file.name} aria-label="下载"><Download /></a>
    </div>
  );
}
