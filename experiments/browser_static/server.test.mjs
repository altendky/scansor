import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink, lstat } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { sha256, verifySite, startPreview, prepareReports } from './server.mjs';

const scratch = process.env.SCANSOR_TEST_TMPDIR || tmpdir();
async function fixture() {
  const root = await mkdtemp(path.join(scratch, 'static-fixture-'));
  await mkdir(path.join(root, 'app'));
  const files = {
    'index.html': Buffer.from('<!doctype html><title>landing fixture</title>'),
    'app/index.html': Buffer.from('<!doctype html><title>fixture</title>'),
    'app/main.js': Buffer.from('export const ready = true;'),
    'app/test.wasm': Buffer.from([0, 97, 115, 109, 1, 0, 0, 0]),
  };
  for (const [relative, bytes] of Object.entries(files)) await writeFile(path.join(root, relative), bytes);
  const manifest = {
    schema_version: 1, kind: 'scansor-static-app', entry: 'app/index.html',
    assets: Object.entries(files).map(([relative, bytes]) => ({
      path: relative, bytes: bytes.length, sha256: sha256(bytes),
    })),
  };
  await writeFile(path.join(root, 'manifest.json'), JSON.stringify(manifest));
  return { root, manifest, files, close: () => rm(root, { recursive: true, force: true }) };
}

test('allowlist serves nested app paths with MIME, isolation and HEAD', async () => {
  const item = await fixture();
  let preview;
  try {
    await writeFile(path.join(item.root, 'unlisted.txt'), 'unlisted');
    preview = await startPreview(await verifySite(item.root), { prefix: '/nested/', isolation: true });
    const landing = await fetch(preview.origin + '/nested/');
    assert.equal(landing.status, 200);
    assert.match(await landing.text(), /landing fixture/);
    let response = await fetch(preview.origin + '/nested/app/');
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /^text\/html/);
    assert.equal(response.headers.get('cross-origin-opener-policy'), 'same-origin');
    assert.equal(response.headers.get('cross-origin-embedder-policy'), 'require-corp');
    response = await fetch(preview.origin + '/nested/app/test.wasm', { method: 'HEAD' });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('content-type'), 'application/wasm');
    assert.equal((await response.arrayBuffer()).byteLength, 0);
    for (const url of ['/nested/unlisted.txt', '/nested/api/meta', '/app/', '/nested/%2e%2e/unlisted.txt'])
      assert.equal((await fetch(preview.origin + url)).status, 404);
    assert.equal((await fetch(preview.origin + '/nested/app/main.js', { method: 'POST' })).status, 405);
  } finally { if (preview) await preview.close(); await item.close(); }
});

test('corrupted bytes, duplicate or escaping records fail before serving', async () => {
  const item = await fixture();
  try {
    await writeFile(path.join(item.root, 'app/main.js'), 'corrupt');
    await assert.rejects(verifySite(item.root), /integrity failure/);
    await writeFile(path.join(item.root, 'app/main.js'), item.files['app/main.js']);
    item.manifest.assets.push(item.manifest.assets[0]);
    await writeFile(path.join(item.root, 'manifest.json'), JSON.stringify(item.manifest));
    await assert.rejects(verifySite(item.root), /duplicate/);
    item.manifest.assets.pop();
    item.manifest.assets[0].path = '../escape';
    await writeFile(path.join(item.root, 'manifest.json'), JSON.stringify(item.manifest));
    await assert.rejects(verifySite(item.root), /Invalid/);
  } finally { await item.close(); }
});

test('symlinks fail initial validation and replacement after startup', async () => {
  const item = await fixture();
  let preview;
  try {
    preview = await startPreview(await verifySite(item.root));
    await rm(path.join(item.root, 'app/main.js'));
    await symlink('index.html', path.join(item.root, 'app/main.js'));
    await assert.rejects(verifySite(item.root), /symlink/);
    assert.equal((await fetch(preview.origin + '/app/main.js')).status, 500);
  } finally { if (preview) await preview.close(); await item.close(); }
});

test('manifest and content changes after startup fail closed', async () => {
  const item = await fixture();
  let preview;
  try {
    preview = await startPreview(await verifySite(item.root));
    await writeFile(path.join(item.root, 'app/main.js'), 'changed');
    assert.equal((await fetch(preview.origin + '/app/main.js')).status, 500);
    await writeFile(path.join(item.root, 'manifest.json'), JSON.stringify({ ...item.manifest, extra: 'changed' }));
    assert.equal((await fetch(preview.origin + '/manifest.json')).status, 500);
  } finally { if (preview) await preview.close(); await item.close(); }
});

test('reports cannot alter artifact/repository through direct or symlinked parents', async () => {
  const artifact = await fixture(), repository = await fixture();
  const parent = await mkdtemp(path.join(scratch, 'static-reports-parent-'));
  const excluded = [artifact.root, repository.root];
  try {
    for (const root of excluded) {
      await assert.rejects(prepareReports(path.join(root, 'new-reports'), excluded), /outside/);
      const link = path.join(parent, path.basename(root));
      await symlink(root, link);
      await assert.rejects(prepareReports(path.join(link, 'new-parent', 'reports'), excluded), /inside/);
      await assert.rejects(lstat(path.join(root, 'new-parent')), { code: 'ENOENT' });
    }
    const reports = path.join(parent, 'allowed-parent', 'reports');
    assert.equal(await prepareReports(reports, excluded), reports);
    await assert.rejects(prepareReports(reports, excluded), { code: 'EEXIST' });
  } finally {
    await rm(parent, { recursive: true, force: true });
    await artifact.close();
    await repository.close();
  }
});
