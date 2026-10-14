import { createHash } from 'node:crypto';
import { readFile, lstat, realpath, mkdir } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';

export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const contains = (root, target) => target === root || target.startsWith(root + path.sep);
const safeRelative = value => typeof value === 'string' && value.length > 0 &&
  !value.startsWith('/') && !/[\\?#:\0]/.test(value) &&
  value.split('/').every(part => part && part !== '.' && part !== '..');

async function checkedFile(root, relative) {
  if (!safeRelative(relative)) throw new Error(`Invalid artifact path: ${relative}`);
  let current = root;
  for (const component of relative.split('/')) {
    current = path.join(current, component);
    const metadata = await lstat(current);
    if (metadata.isSymbolicLink()) throw new Error(`Artifact symlink is forbidden: ${relative}`);
  }
  if (!(await lstat(current)).isFile()) throw new Error(`Artifact asset is not a file: ${relative}`);
  const resolved = await realpath(current);
  if (!resolved.startsWith(root + path.sep)) throw new Error(`Artifact path escapes root: ${relative}`);
  return readFile(current);
}

export async function prepareReports(directory, excludedDirectories) {
  const output = path.resolve(directory);
  const excluded = await Promise.all(excludedDirectories.map(directory => realpath(directory)));
  if (excluded.some(root => contains(root, output)))
    throw new Error('Reports must be outside the artifact and repository');
  // Resolve an existing ancestor before mkdir, so a symlinked parent cannot
  // cause even report-directory creation to modify an excluded directory.
  let ancestor = path.dirname(output), canonicalAncestor;
  while (!canonicalAncestor) {
    try { canonicalAncestor = await realpath(ancestor); }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      const parent = path.dirname(ancestor);
      if (parent === ancestor) throw error;
      ancestor = parent;
    }
  }
  const prospective = path.join(canonicalAncestor, path.relative(ancestor, output));
  if (excluded.some(root => contains(root, prospective)))
    throw new Error('Reports resolve inside the artifact or repository');
  await mkdir(path.dirname(output), { recursive: true });
  await mkdir(output); // Existing report paths fail; never overwrite old reports.
  const canonicalOutput = await realpath(output);
  if ((await lstat(output)).isSymbolicLink() || excluded.some(root => contains(root, canonicalOutput)))
    throw new Error('Created reports directory resolves inside the artifact or repository');
  return canonicalOutput;
}

export async function verifySite(directory) {
  const given = path.resolve(directory);
  if ((await lstat(given)).isSymbolicLink()) throw new Error('Artifact root must not be a symlink');
  const root = await realpath(given);
  const manifestBytes = await checkedFile(root, 'manifest.json');
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  if (manifest.schema_version !== 1 || manifest.kind !== 'scansor-static-app' ||
      !Array.isArray(manifest.assets))
    throw new Error('Expected a schema-1 scansor-static-app site manifest');
  const records = new Map();
  for (const record of manifest.assets) {
    if (!safeRelative(record.path) || record.path === 'manifest.json' || records.has(record.path) ||
        !Number.isSafeInteger(record.bytes) || record.bytes < 0 || !/^[a-f0-9]{64}$/.test(record.sha256))
      throw new Error(`Invalid/duplicate asset record: ${record.path}`);
    const bytes = await checkedFile(root, record.path);
    if (bytes.length !== record.bytes || sha256(bytes) !== record.sha256)
      throw new Error(`Artifact integrity failure: ${record.path}`);
    records.set(record.path, record);
  }
  if (!safeRelative(manifest.entry) || !records.has(manifest.entry))
    throw new Error('Manifest entry must be a listed asset');
  return { root, manifest, manifestBytes, manifest_sha256: sha256(manifestBytes), records };
}

const contentTypes = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json', '.wasm': 'application/wasm', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8', '.zip': 'application/zip',
};

export async function startPreview(site, { port = 0, prefix = '/', isolation = false } = {}) {
  if (!prefix.startsWith('/') || !prefix.endsWith('/') || prefix.includes('..') || /[\\?#\0]/.test(prefix))
    throw new Error('Preview prefix must be a clean absolute URL directory');
  const requests = [];
  const server = http.createServer(async (request, response) => {
    try {
      if (!['GET', 'HEAD'].includes(request.method)) {
        response.writeHead(405, { Allow: 'GET, HEAD' }).end();
        return;
      }
      const pathname = decodeURIComponent(new URL(request.url, 'http://preview.invalid').pathname);
      if (!pathname.startsWith(prefix)) { response.writeHead(404).end(); return; }
      let relative = pathname.slice(prefix.length);
      if (!relative) relative = 'index.html';
      if (relative.endsWith('/')) relative += 'index.html';
      if (relative !== 'manifest.json' && !site.records.has(relative)) {
        requests.push({ path: relative, status: 404 });
        response.writeHead(404).end();
        return;
      }
      const bytes = await checkedFile(site.root, relative);
      const record = site.records.get(relative);
      if (record && (bytes.length !== record.bytes || sha256(bytes) !== record.sha256))
        throw new Error(`Artifact changed during preview: ${relative}`);
      if (!record && sha256(bytes) !== site.manifest_sha256)
        throw new Error('Site manifest changed during preview');
      const headers = {
        'Content-Type': contentTypes[path.extname(relative)] || 'application/octet-stream',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      };
      if (isolation) {
        headers['Cross-Origin-Opener-Policy'] = 'same-origin';
        headers['Cross-Origin-Embedder-Policy'] = 'require-corp';
      }
      requests.push({ path: relative, status: 200, bytes: bytes.length });
      response.writeHead(200, headers);
      response.end(request.method === 'HEAD' ? undefined : bytes);
    } catch (error) {
      requests.push({ path: request.url, status: 500, error: String(error) });
      response.writeHead(500).end('Static artifact verification failed');
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', resolve);
  });
  return {
    server, requests, origin: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise(resolve => server.close(resolve)),
  };
}
