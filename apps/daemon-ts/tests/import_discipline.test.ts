/**
 * 导入纪律测试锚（任务书裁决 #6/#7）：
 * ① 对 @coagentia/contracts-ts 只许 `import type`（node 直跑无法运行时解析该包）——
 *    静态语句（单/双引号、多行块）与动态 import()（两种引号）一并封堵；
 * ② 包内相对导入必须显式 .ts 扩展名（type stripping 硬要求）——覆盖三形态：
 *    静态 `from '…'`、副作用 `import '…'`、动态 `import('…')`。
 * tsconfig verbatimModuleSyntax 只挡「类型被值导入」，挡不住真值导入——本锚兜底。
 * 子路径导入（@coagentia/contracts-ts/src/…）已由 tsc 按包 exports-map 拒绝（实测），
 * 无需在此重复扫描。
 *
 * 逃逸面加固（CR 批）：原正则锚单引号 + 仅 `from '` 形态——双引号包导入、无扩展副作用
 * 导入、动态 import 均可静默穿透直达运行时；现引号类 ['"] 全覆盖 + 三形态逐条封堵，
 * 并以 fixture 字符串自测（每类逃逸必被捕、每类合法写法必放行，防正则回归）。
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

/**
 * contracts-ts 静态值导入计数：语句级匹配（^import + 无分号跨越 → 多行块也从语句起点命中），
 * 引号类 ['"]；`import type …`（含多行）合法，其余（值/混合/默认导入）计违规。
 */
function contractsValueImports(text: string): number {
  const anyImport = text.match(/^import\s[^;]*?from\s+['"]@coagentia\/contracts-ts['"]/gm) ?? [];
  const typeImport = anyImport.filter((s) => /^import\s+type\b/.test(s));
  return anyImport.length - typeImport.length;
}

/** contracts-ts 动态 import（两种引号、括号后空白容忍）——类型擦除救不了运行时解析，一律禁。 */
const CONTRACTS_DYNAMIC_RE = /import\(\s*['"]@coagentia\/contracts-ts/;

// 相对导入缺 .ts 的三形态（逐行检；引号类 ['"] 全覆盖）：
const RELATIVE_IMPORT_RES: readonly RegExp[] = [
  /from\s+['"](\.[^'"]+)['"]/, // 静态 `import … from './x'`
  /^import\s+['"](\.[^'"]+)['"]/, // 副作用 `import './x'`（无 from，原规则漏检面）
  /import\(\s*['"](\.[^'"]+)['"]/, // 动态 `import('./x')`
];

/** 相对导入缺 .ts 扩展名的违规行清单（"行号: 行文本"）。 */
function relativeNoTsViolations(text: string): string[] {
  const out: string[] = [];
  for (const [i, line] of text.split('\n').entries()) {
    for (const re of RELATIVE_IMPORT_RES) {
      const m = re.exec(line);
      if (m && !m[1]!.endsWith('.ts')) {
        out.push(`${i + 1}: ${line.trim()}`);
        break; // 同行多形态只报一次
      }
    }
  }
  return out;
}

describe('导入纪律', () => {
  it('src 对 contracts-ts 只许 import type（语句级解析，单/双引号、单/多行皆可）', () => {
    const violations: string[] = [];
    for (const f of walk(SRC)) {
      const text = fs.readFileSync(f, 'utf-8');
      const bad = contractsValueImports(text);
      if (bad > 0) {
        violations.push(`${path.relative(SRC, f)}: ${bad} 处值导入`);
      }
      if (CONTRACTS_DYNAMIC_RE.test(text)) {
        violations.push(`${path.relative(SRC, f)}: 动态 import()`);
      }
    }
    expect(violations).toEqual([]);
  });

  it('src 包内相对导入显式 .ts 扩展名（静态 from / 副作用 import / 动态 import 三形态）', () => {
    const violations: string[] = [];
    for (const f of walk(SRC)) {
      const text = fs.readFileSync(f, 'utf-8');
      for (const v of relativeNoTsViolations(text)) {
        violations.push(`${path.relative(SRC, f)}:${v}`);
      }
    }
    expect(violations).toEqual([]);
  });

  // ---------------------------------------------------------------- 正则自测（fixture 字符串，
  // 非真实文件）：每类逃逸必被捕、每类合法写法必放行——防未来收紧/放宽正则时静默回归。

  it('自测：逃逸形态逐条被捕', () => {
    // (a) 双引号值导入（原规则锚单引号漏检）
    expect(contractsValueImports(`import { X } from "@coagentia/contracts-ts";\n`)).toBe(1);
    // 单引号值导入（既有覆盖保持）
    expect(contractsValueImports(`import { X } from '@coagentia/contracts-ts';\n`)).toBe(1);
    // (c) 动态 import：两种引号
    expect(CONTRACTS_DYNAMIC_RE.test(`await import('@coagentia/contracts-ts');\n`)).toBe(true);
    expect(CONTRACTS_DYNAMIC_RE.test(`await import("@coagentia/contracts-ts");\n`)).toBe(true);
    expect(CONTRACTS_DYNAMIC_RE.test(`await import( '@coagentia/contracts-ts' );\n`)).toBe(true);
    // (b) 副作用相对导入缺 .ts（两种引号）
    expect(relativeNoTsViolations(`import './x';\n`)).toHaveLength(1);
    expect(relativeNoTsViolations(`import "./x";\n`)).toHaveLength(1);
    // 静态 from 相对导入缺 .ts（双引号为原漏检面）
    expect(relativeNoTsViolations(`import { a } from "./x";\n`)).toHaveLength(1);
    // (c) 动态相对导入缺 .ts
    expect(relativeNoTsViolations(`const m = await import('./x');\n`)).toHaveLength(1);
    expect(relativeNoTsViolations(`const m = await import("../y/z");\n`)).toHaveLength(1);
  });

  it('自测：合法形态逐条放行', () => {
    // import type：单行（两种引号）
    expect(contractsValueImports(`import type { A } from '@coagentia/contracts-ts';\n`)).toBe(0);
    expect(contractsValueImports(`import type { A } from "@coagentia/contracts-ts";\n`)).toBe(0);
    // import type：多行块（语句级正则 [^;]*? 跨行，既有性质保持）
    expect(
      contractsValueImports(
        `import type {\n  A,\n  B,\n} from '@coagentia/contracts-ts';\n`,
      ),
    ).toBe(0);
    // 深层相对导入带 .ts
    expect(relativeNoTsViolations(`import { a } from './deep/nested/mod.ts';\n`)).toEqual([]);
    // 副作用导入带 .ts
    expect(relativeNoTsViolations(`import './side-effect.ts';\n`)).toEqual([]);
    // 动态相对导入带 .ts
    expect(relativeNoTsViolations(`const m = await import('./x.ts');\n`)).toEqual([]);
    // 非相对（包名）导入不归 .ts 规则管
    expect(relativeNoTsViolations(`import * as fs from 'node:fs';\n`)).toEqual([]);
  });
});
