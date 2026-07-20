import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { chromium } from 'playwright';

function git(cwd: string, args: readonly string[]): string {
  const result = spawnSync('git', args, {
    cwd,
    encoding: 'utf8',
    windowsHide: true,
  });
  if (result.error !== undefined) throw result.error;
  assert.equal(result.status, 0, `git ${args.join(' ')} failed: ${result.stderr}`);
  return result.stdout.trim();
}

async function main(): Promise<void> {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-p0-git-'));
  try {
    git(fixture, ['init', '--initial-branch=main']);
    git(fixture, ['config', 'user.name', 'CoAgentia P0 CI']);
    git(fixture, ['config', 'user.email', 'p0-ci@coagentia.invalid']);
    fs.writeFileSync(path.join(fixture, '空格-路径.txt'), 'P0 Git integration\n', 'utf8');
    git(fixture, ['add', '--', '空格-路径.txt']);
    git(fixture, ['commit', '-m', 'P0 unicode path smoke']);
    assert.match(git(fixture, ['rev-parse', 'HEAD']), /^[0-9a-f]{40,64}$/u);
  } finally {
    fs.rmSync(fixture, { recursive: true, force: true });
  }

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<!doctype html><title>CoAgentia P0</title><main data-gate="browser">PASS</main>');
    assert.equal(await page.title(), 'CoAgentia P0');
    assert.equal(await page.locator('main[data-gate="browser"]').textContent(), 'PASS');
  } finally {
    await browser.close();
  }

  process.stdout.write('p0-integration-smoke: PASS (git + chromium)\n');
}

await main();
