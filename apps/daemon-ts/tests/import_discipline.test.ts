/**
 * 导入纪律测试锚（任务书裁决 #6/#7）：
 * ① 对 @coagentia/contracts-ts 只许 `import type`（node 直跑无法运行时解析该包）；
 * ② 包内相对导入必须显式 .ts 扩展名（type stripping 硬要求）。
 * tsconfig verbatimModuleSyntax 只挡「类型被值导入」，挡不住真值导入——本锚兜底。
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

import { describe, expect, it } from 'vitest';

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) out.push(...walk(p));
    else if (name.endsWith('.ts')) out.push(p);
  }
  return out;
}

const SRC = path.join(import.meta.dirname, '..', 'src');

describe('导入纪律', () => {
  it('src 对 contracts-ts 只许 import type（语句级解析，单/多行皆可）', () => {
    const violations: string[] = [];
    for (const f of walk(SRC)) {
      const text = fs.readFileSync(f, 'utf-8');
      // 值导入或动态 import 该包 = 违规；`import type {...} from '...'`（含多行块）合法。
      // 锚在语句起点（^import + 无分号跨越），防从文件首个 import 起懒惰匹配跨语句误报。
      const anyImport = text.match(/^import\s[^;]*?from\s+'@coagentia\/contracts-ts'/gm) ?? [];
      const typeImport = anyImport.filter((s) => /^import\s+type\b/.test(s));
      if (anyImport.length !== typeImport.length) {
        violations.push(`${path.relative(SRC, f)}: ${anyImport.length - typeImport.length} 处值导入`);
      }
      if (text.includes("import('@coagentia/contracts-ts')")) {
        violations.push(`${path.relative(SRC, f)}: 动态 import()`);
      }
    }
    expect(violations).toEqual([]);
  });

  it('src 包内相对导入显式 .ts 扩展名', () => {
    const violations: string[] = [];
    for (const f of walk(SRC)) {
      const text = fs.readFileSync(f, 'utf-8');
      for (const [i, line] of text.split('\n').entries()) {
        const m = /from\s+'(\.[^']*)'/.exec(line);
        if (m && !m[1]!.endsWith('.ts')) {
          violations.push(`${path.relative(SRC, f)}:${i + 1}: ${line.trim()}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
