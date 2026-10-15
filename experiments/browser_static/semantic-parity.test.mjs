import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { compareSemanticBatch } from './semantic-parity.mjs';
import { sha256 } from './server.mjs';

async function fixture() {
  const root = await mkdtemp(path.join(process.env.SCANSOR_TEST_TMPDIR || tmpdir(), 'semantic-bridge-'));
  await mkdir(path.join(root, 'runtime'));
  await mkdir(path.join(root, 'reports'));
  const helper = await readFile(new URL('../browser_runtime/semantic_parity.py', import.meta.url));
  const manifest = { source_files: [{ archive_path: 'experiments/browser_runtime/semantic_parity.py', sha256: sha256(helper) }] };
  await writeFile(path.join(root, 'runtime/manifest.json'), JSON.stringify(manifest));
  return { root, manifest, options: { site: { root, manifest: { runtime: { path: 'runtime' } } },
    nativePython: process.env.SCANSOR_NATIVE_PYTHON || 'python3', output: path.join(root, 'reports'), report: {} },
  close: () => rm(root, { recursive: true, force: true }) };
}

test('bridge runs the trusted policy and retains exact full input and bound receipt', async () => {
  const item = await fixture();
  try {
    const cases = [{ id: 'strict', profile: 'values', actual: { residuals: [1, 2] }, expected: { residuals: [1, 2] } }];
    const receipt = await compareSemanticBatch({ ...item.options, cases });
    assert.equal(receipt.semantic_complete, true);
    assert.equal(receipt.budgets_validated, true);
    assert.deepEqual(JSON.parse(item.options.report.semantic_parity_evidence.json), { cases });
    assert.equal(receipt.input_sha256, item.options.report.semantic_parity_evidence.sha256);
    assert.equal(await readFile(path.join(item.root, 'reports/semantic-parity-input.json'), 'utf8'), item.options.report.semantic_parity_evidence.json);
  } finally { await item.close(); }
});

test('meaningful mismatch rejects while preserving complete inputs and failed receipt', async () => {
  const item = await fixture();
  try {
    const cases = [{ id: 'changed-geometry', actual: { radius: 3 }, expected: { radius: 2 } }];
    await assert.rejects(compareSemanticBatch({ ...item.options, cases }), /Semantic comparison failed/);
    assert.equal(item.options.report.semantic_parity.semantic_complete, false);
    assert.deepEqual(JSON.parse(item.options.report.semantic_parity_evidence.json), { cases });
    assert.equal(item.options.report.semantic_parity.input_sha256, item.options.report.semantic_parity_evidence.sha256);
  } finally { await item.close(); }
});

test('different packaged policy fails before invoking the comparator', async () => {
  const item = await fixture();
  try {
    item.manifest.source_files[0].sha256 = 'a'.repeat(64);
    await writeFile(path.join(item.root, 'runtime/manifest.json'), JSON.stringify(item.manifest));
    await assert.rejects(compareSemanticBatch({ ...item.options, cases: [] }), /differs from reviewed/);
    assert.equal(item.options.report.semantic_parity, undefined);
  } finally { await item.close(); }
});

test('missing interpreter retains raw evidence and fails without success receipt', async () => {
  const item = await fixture();
  try {
    const cases = [{ id: 'preserve', actual: [1], expected: [1] }];
    await assert.rejects(compareSemanticBatch({ ...item.options, nativePython: path.join(item.root, 'absent'), cases }), /ENOENT/);
    assert.deepEqual(JSON.parse(item.options.report.semantic_parity_evidence.json), { cases });
    assert.equal(item.options.report.semantic_parity, undefined);
    assert.match(item.options.report.semantic_parity_receipt_error, /ENOENT/);
  } finally { await item.close(); }
});
