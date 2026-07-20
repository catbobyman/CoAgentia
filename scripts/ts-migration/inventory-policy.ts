import {
  inventoryScanScopeSha256,
  resolveInventoryBaselineTree,
  type DiscoveredInventoryEntry,
  type InventoryScan,
  type MigrationInventoryRecord,
} from './migration-inventory.ts';

interface P0Decision {
  owner: string;
  disposition: MigrationInventoryRecord['disposition'];
  target_phase: string | null;
  target: string | null;
  rationale: string;
}

export interface P0MigrationInventoryRecord extends MigrationInventoryRecord {
  source_reasons: string[];
  rationale: string;
}

function replacement(owner: string, phase: string, target: string, rationale: string): P0Decision {
  return { owner, disposition: 'replace', target_phase: phase, target, rationale };
}

function retirement(owner: string, phase: string, rationale: string): P0Decision {
  return { owner, disposition: 'retire', target_phase: phase, target: null, rationale };
}

function fileDecision(entry: DiscoveredInventoryEntry): P0Decision {
  const file = entry.path;
  if (file.startsWith('apps/daemon/')) {
    return retirement('owner:A3-daemon-retirement', 'A3', 'daemon-ts 已承接产品职责；A3 在测试母账与观察门通过后删除旧 Python daemon');
  }
  if (file === 'apps/server/migrations/script.py.mako') {
    return replacement('owner:B1-database', 'E', 'apps/server-ts migration ledger/template', 'B1 建立 TS 数据迁移能力；该 Python 模板保留到 D 回滚期结束，E 清场');
  }
  if (file.startsWith('apps/server/')) {
    return replacement('owner:B0-B10-server', 'E', 'apps/server-ts equivalent owned by B0-B10', 'B0-B10 逐域替代；Python server 作为 D 回滚实现保留到 E');
  }
  if (file.startsWith('packages/contracts/')) {
    return replacement('owner:P2-contracts', 'E', 'packages/contracts-ts TS-authored contract authority', 'P2 建立 TS 契约权威；Python oracle 保留到 D，E 删除');
  }
  if (file.startsWith('apps/mock-server/')) {
    return replacement('owner:C1-mock', 'C1', 'TS contract registry backed mock or approved responsibility replacement', 'C1 以真实消费者证据决定 TS 重写或职责替代；当前保守登记 replace');
  }
  if (file.startsWith('scripts/')) {
    return replacement('owner:P2-C2-tooling', 'C2', 'TypeScript generator/tooling entry', 'P2 迁移生成职责，C2 复核调用方并删除旧入口');
  }
  if (file === 'packages/contracts-ts/gen.mjs') {
    return replacement('owner:P2-contracts', 'C2', 'packages/contracts-ts/gen.ts', 'P2 建立纯 TS 生成链，C2 完成全入口清场');
  }
  if (file.startsWith('scratchpad/')) {
    const target = file.replace(/\.(?:py|mjs|ps1)$/iu, '.ts');
    return replacement('owner:C2-probes', 'C2', target, 'P0 保守保留探针责任并迁为 TS；如需退役，C2 必须另附 owner/reviewer 证据');
  }
  return replacement('owner:C2-discovered-entry', 'C2', `${file}.ts`, 'P0 全扩展扫描发现的非 TS 第一方源码归 C2 处置');
}

function entrypointDecision(entry: DiscoveredInventoryEntry): P0Decision {
  const id = entry.id;
  const detail = entry.detail ?? '';
  if (entry.kind === 'pyproject-script') {
    if (/coagentia_server/iu.test(detail)) {
      return replacement('owner:B0-B10-server', 'E', 'compiled @coagentia/server entry', 'TS server 在 B10 就绪并经 D 观察后于 E 替换 Python console entry');
    }
    if (/coagentia_daemon/iu.test(detail)) {
      return replacement('owner:A1-A3-daemon', 'A3', 'verified compiled daemon package entry', 'A1/A2 完成分发和观察，A3 删除 Python console entry');
    }
    return replacement('owner:C1-mock', 'C1', 'TS mock entry or approved replacement', 'C1 处置 Python mock console entry');
  }
  if (entry.kind === 'inline-script') {
    return replacement('owner:C2-discovered-entry', 'C2', 'site/index.ts compiled asset', 'HTML 内联第一方 JavaScript 迁为可类型检查的 TS 源');
  }
  if (entry.kind === 'doc-command') {
    return replacement('owner:E-documentation', 'E', 'pure-TS command documented at the same active entry', '活跃文档中的 Python 工具链命令必须随最终清场改为纯 TS');
  }
  if (entry.kind === 'ci-run') {
    return replacement('owner:C3-CI', 'E', 'pure-TS CI step', 'legacy oracle job在 Python 清场前保留，E 后不得继续调用 Python 工具链');
  }
  if (entry.kind === 'package-script') {
    if (id === 'package-script:package.json#gen:schemas') {
      return replacement('owner:P2-contracts', 'P2', 'pure-TS pnpm gen pipeline', 'P2 将 schema/OpenAPI 生成权威迁到 TypeScript');
    }
    if (id === 'package-script:packages/contracts-ts/package.json#gen') {
      return replacement('owner:P2-contracts', 'P2', 'node gen.ts', 'P2 把 gen.mjs 改为 TS authored generator');
    }
    return replacement('owner:E-toolchain', 'E', 'pure-TS package script', 'Python/非 TS 工具链命令最迟在 E 清场替换');
  }
  if (entry.kind === 'package-bin') {
    return replacement('owner:A1-distribution', 'A1', 'compiled JavaScript package bin', '源码态 TS bin 在 A1 分发校准中改为离仓可执行构建产物');
  }
  return replacement('owner:C2-discovered-entry', 'C2', 'TypeScript executable/config equivalent', '全入口扫描发现项归 C2 清场');
}

function decide(entry: DiscoveredInventoryEntry): P0Decision {
  if (entry.id === 'package-script:package.json#p0:oracle:build' || entry.id === 'package-script:package.json#verify:oracle-collection') {
    return retirement('owner:E-toolchain', 'E', '冻结 oracle 保留为制品；Python 删除后不再执行 pytest collection');
  }
  if (!entry.migration_residual) {
    return {
      owner: 'owner:repository-maintainers',
      disposition: 'keep',
      target_phase: null,
      target: null,
      rationale: '当前已是 TypeScript/Node 入口或平台配置；内容 fingerprint 继续受 inventory 门保护',
    };
  }
  if (entry.kind === 'file') return fileDecision(entry);
  return entrypointDecision(entry);
}

export function buildP0MigrationInventory(
  scan: InventoryScan,
  baselineSha: string,
): Record<string, unknown> & { entries: P0MigrationInventoryRecord[] } {
  if (baselineSha !== scan.generated_from_head) {
    throw new Error(`inventory baseline must equal current HEAD: ${baselineSha} != ${scan.generated_from_head}`);
  }
  return {
    schema_version: 1,
    kind: 'coagentia.migration-inventory',
    policy_version: 'p0-v1',
    baseline_sha: baselineSha,
    baseline_tree_sha: resolveInventoryBaselineTree(scan.repo_root, baselineSha),
    scan_scope_sha256: inventoryScanScopeSha256(scan.entries),
    entries: scan.entries.map((entry) => {
      const decision = decide(entry);
      return {
        id: entry.id,
        kind: entry.kind,
        path: entry.path,
        file_mode: entry.file_mode,
        git_blob: entry.git_blob,
        ...(entry.fingerprint === undefined ? {} : { fingerprint: entry.fingerprint }),
        owner: decision.owner,
        disposition: decision.disposition,
        target_phase: decision.target_phase,
        target: decision.target,
        source_reasons: entry.reasons,
        rationale: decision.rationale,
      };
    }),
  };
}
