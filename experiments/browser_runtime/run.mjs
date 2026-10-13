import { createHash } from 'node:crypto';
import { readFile, writeFile, stat } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { assetURL } from './integrity.mjs';

const require = createRequire(new URL('../browser_viewer/package.json', import.meta.url));
const playwright = require('playwright');
const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`Missing ${name} value`);
  return args[index + 1];
};
const directoryArgument = argument('--directory');
if (!directoryArgument) throw new Error('Usage: node experiments/browser_runtime/run.mjs --directory <artifact> [--browser chromium|firefox|webkit] [--cad]');
const directory = path.resolve(directoryArgument);
const browserName = argument('--browser', 'chromium');
if (!['chromium', 'firefox', 'webkit'].includes(browserName)) throw new Error(`Unsupported browser: ${browserName}`);
const prefix = '/runtime-probe/';
const manifestBytes = await readFile(path.join(directory, 'manifest.json'));
const manifest = JSON.parse(manifestBytes);
if (manifest.schema_version !== 1) throw new Error('Unsupported artifact manifest');
if (args.includes('--cad') && !manifest.cad) throw new Error('Rebuild artifact with --cad for the optional CAD smoke test');
// Detect corruption before startup, including the worker code and notice assets.
for (const record of manifest.assets) {
  assetURL(record.path, 'https://artifact.invalid/');
  const data = await readFile(path.join(directory, record.path));
  if (data.byteLength !== record.bytes || createHash('sha256').update(data).digest('hex') !== record.sha256) throw new Error(`Asset integrity failure: ${record.path}`);
}

const requests = [];
const types = { '.html': 'text/html', '.mjs': 'text/javascript', '.json': 'application/json', '.wasm': 'application/wasm', '.md': 'text/plain' };
const allowed = new Set(['manifest.json', ...manifest.assets.map(r => r.path)]);
const server = http.createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, 'http://local.invalid').pathname);
    const relative = pathname.startsWith(prefix) ? pathname.slice(prefix.length) || 'index.html' : '';
    if (!allowed.has(relative)) { response.writeHead(404).end(); return; }
    const filename = path.join(directory, relative);
    if (!(await stat(filename)).isFile()) { response.writeHead(404).end(); return; }
    const data = await readFile(filename);
    requests.push({ path: relative, bytes: data.length });
    response.writeHead(200, { 'Content-Type': types[path.extname(filename)] || 'application/octet-stream', 'Cache-Control': 'public, max-age=3600' });
    response.end(data);
  } catch { response.writeHead(500).end(); }
});
let browser;
try {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await playwright[browserName].launch({ headless: true });
  const page = await browser.newPage();
  const unexpectedRequests = [];
  await page.route('**/*', async route => {
    if (!route.request().url().startsWith(`${origin}/`)) {
      unexpectedRequests.push(route.request().url());
      await route.abort();
    } else await route.continue();
  });
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(String(error)));
  await page.goto(`${origin}${prefix}${args.includes('--cad') ? '?cad' : ''}`);
  await page.waitForFunction(() => window.finished, null, { timeout: 180_000 });
  const result = await page.evaluate(() => ({ finished: window.finished, phases: window.events, heartbeat: window.heartbeat, user_agent: navigator.userAgent, cross_origin_isolated: crossOriginIsolated }));
  result.browser = { name: browserName, version: browser.version() };
  result.manifest_sha256 = createHash('sha256').update(manifestBytes).digest('hex');
  result.requests = requests;
  result.served_bytes = requests.reduce((sum, request) => sum + request.bytes, 0);
  result.unexpected_requests = unexpectedRequests;
  result.page_errors = pageErrors;
  if (result.finished.cad?.result.step_base64) {
    await writeFile(path.join(directory, `browser-${browserName}.step`), Buffer.from(result.finished.cad.result.step_base64, 'base64'));
    delete result.finished.cad.result.step_base64;
    // Reopen browser-produced STEP using the pinned native environment and the packaged probe.
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
    const executable = argument('--native-python', path.join(root, '.venv/bin/python'));
    const code = "import sys,tempfile,zipfile,json; from pathlib import Path; t=tempfile.TemporaryDirectory(dir=sys.argv[1]); zipfile.ZipFile(Path(sys.argv[1])/'scansor.zip').extractall(t.name); sys.path.insert(0,t.name); from experiments.browser_runtime.probe import read_step; r=read_step(Path(sys.argv[1])/sys.argv[2]); print('READBACK:'+json.dumps(r)); t.cleanup()";
    const readback = spawnSync(executable, ['-I', '-c', code, directory, `browser-${browserName}.step`], { cwd: directory, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', TMPDIR: directory }, encoding: 'utf8', timeout: 60_000 });
    if (readback.error || readback.status !== 0) throw new Error(`Native STEP readback failed: ${readback.error || readback.stderr || readback.stdout}`);
    const line = readback.stdout.split('\n').find(line => line.startsWith('READBACK:'));
    if (!line) throw new Error('Missing native STEP readback result');
    result.native_step_readback = JSON.parse(line.slice('READBACK:'.length));
  }
  await writeFile(path.join(directory, `browser-${browserName}.json`), JSON.stringify(result, null, 2) + '\n');
  if (!result.finished.ok || pageErrors.length || unexpectedRequests.length) throw new Error(JSON.stringify({ error: result.finished.error, pageErrors, unexpectedRequests }));
  console.log(JSON.stringify({ browser: result.browser, counts: result.finished.numeric.result.counts, numeric_ms: result.finished.numeric_ms, served_bytes: result.served_bytes, heartbeat: result.heartbeat, cad: result.finished.cad?.result, report: path.join(directory, `browser-${browserName}.json`) }, null, 2));
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
