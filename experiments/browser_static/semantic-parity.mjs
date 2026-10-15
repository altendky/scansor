// The reviewed Python policy is the sole numerical/semantic comparison oracle.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { sha256 } from './server.mjs';

const helper = fileURLToPath(new URL('../browser_runtime/semantic_parity.py', import.meta.url));
const archivePath = 'experiments/browser_runtime/semantic_parity.py';

export async function compareSemanticBatch({ cases, nativePython, site, output, report }) {
  const runtimePath = path.join(site.root, site.manifest.runtime.path);
  const manifest = JSON.parse(await readFile(path.join(runtimePath, 'manifest.json'), 'utf8'));
  const records = manifest.source_files.filter(record => record.archive_path === archivePath);
  assert.equal(records.length, 1, 'Packaged semantic policy must have one source identity');
  const trustedHash = sha256(await readFile(helper));
  assert.equal(records[0].sha256, trustedHash, 'Packaged policy differs from reviewed harness policy');
  const input = JSON.stringify({ cases });
  report.semantic_parity_evidence = { json: input, bytes: Buffer.byteLength(input), sha256: sha256(Buffer.from(input)) };
  const inputPath = path.join(output, 'semantic-parity-input.json');
  const receiptPath = path.join(output, 'semantic-parity-receipt.json');
  await writeFile(inputPath, input, { flag: 'wx' });
  let exit;
  try {
    exit = await new Promise((resolve, reject) => {
      const child = spawn(nativePython, ['-I', helper, '--input', inputPath, '--output', receiptPath], {
        cwd: output, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
      });
      let messages = '', timedOut = false;
      const collect = data => { messages = (messages + data.toString()).slice(-12000); };
      child.stdout.on('data', collect); child.stderr.on('data', collect);
      const timer = setTimeout(() => { timedOut = true; child.kill('SIGTERM'); }, 30000);
      let killTimer;
      child.once('spawn', () => { killTimer = setTimeout(() => child.kill('SIGKILL'), 32000); });
      child.once('error', error => { clearTimeout(timer); clearTimeout(killTimer); reject(error); });
      child.once('close', (code, signal) => {
        clearTimeout(timer); clearTimeout(killTimer); resolve({ code, signal, timedOut, messages });
      });
    });
  } finally {
    // The raw batch is retained even if startup, comparison or receipt reading fails.
    try { report.semantic_parity = JSON.parse(await readFile(receiptPath, 'utf8')); }
    catch (error) { report.semantic_parity_receipt_error = String(error); }
  }
  assert.equal(exit.code, 0, `Semantic comparison failed: ${JSON.stringify(exit)}`);
  assert.equal(exit.timedOut, false, 'Semantic comparison exceeded its deadline');
  assert.equal(report.semantic_parity.policy_sha256, trustedHash);
  assert.equal(report.semantic_parity.input_sha256, report.semantic_parity_evidence.sha256);
  assert.equal(report.semantic_parity.semantic_complete, true);
  assert.equal(report.semantic_parity.budgets_validated, true);
  return report.semantic_parity;
}
