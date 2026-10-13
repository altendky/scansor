export function assetURL(relative, base) {
  if (typeof relative !== 'string' || relative.startsWith('/') || relative.split('/').some(p => p === '..' || p === '.') || /[\\?#:]/.test(relative)) {
    throw new Error(`Invalid asset path: ${relative}`);
  }
  return new URL(relative, base);
}

export async function checkedFetch(record, base) {
  const response = await fetch(assetURL(record.path, base));
  if (!response.ok) throw new Error(`Missing asset ${record.path}: HTTP ${response.status}`);
  const data = await response.arrayBuffer();
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(b => b.toString(16).padStart(2, '0')).join('');
  if (data.byteLength !== record.bytes || hash !== record.sha256) throw new Error(`Asset integrity failure: ${record.path}`);
  return data;
}
