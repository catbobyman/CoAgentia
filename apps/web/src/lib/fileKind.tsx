// 文件类型判定的单一事实源(附件卡 P4 与消息流附件卡共用,避免两处 CODE_EXT 漂移导致同名文件图标不一致)。
import { File as FileIcon, FileCode, FileText, Image as ImageIcon } from 'lucide-react';

import type { FilePublic } from '@coagentia/contracts-ts';

import { API_BASE } from '../api';

export type Cat = 'image' | 'doc' | 'code' | 'other';

const CODE_EXT = new Set([
  'html', 'htm', 'css', 'js', 'jsx', 'ts', 'tsx', 'py', 'sh', 'json', 'yml', 'yaml',
  'toml', 'xml', 'rs', 'go', 'java', 'c', 'cpp', 'rb',
]);
const DOC_EXT = new Set(['pdf', 'md', 'markdown', 'txt', 'csv', 'doc', 'docx', 'rtf']);

export function extOf(name: string): string {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i + 1).toLowerCase() : '';
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function categoryOf(f: FilePublic): Cat {
  if (f.mime.startsWith('image/')) return 'image';
  const ext = extOf(f.name);
  if (CODE_EXT.has(ext) || f.mime === 'text/html' || f.mime.includes('javascript')) return 'code';
  if (DOC_EXT.has(ext) || f.mime === 'application/pdf' || f.mime.startsWith('text/')) return 'doc';
  return 'other';
}

export function catIcon(cat: Cat) {
  if (cat === 'image') return <ImageIcon />;
  if (cat === 'code') return <FileCode />;
  if (cat === 'doc') return <FileText />;
  return <FileIcon />;
}

// 文本类内容分流:text/* · json · markdown · csv · log 取正文渲染。
export function isTextPreview(f: FilePublic): boolean {
  const ext = extOf(f.name);
  return (
    f.mime.startsWith('text/') ||
    f.mime === 'application/json' ||
    ['md', 'markdown', 'txt', 'csv', 'json', 'log'].includes(ext)
  );
}

export const contentUrl = (id: string): string => `${API_BASE}/api/files/${id}/content`;
