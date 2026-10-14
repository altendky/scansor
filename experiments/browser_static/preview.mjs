import { verifySite, startPreview } from './server.mjs';

const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`Missing ${name}`);
  return args[index + 1];
};
const directory = argument('--directory');
if (!directory)
  throw new Error('Usage: node preview.mjs --directory <site> [--port 4173] [--prefix /] [--isolation]');
const port = Number(argument('--port', '4173'));
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid --port');
const prefix = argument('--prefix', '/');
const site = await verifySite(directory);
const preview = await startPreview(site, { port, prefix, isolation: args.includes('--isolation') });
console.log(JSON.stringify({
  url: preview.origin + prefix + site.manifest.entry,
  manifest_sha256: site.manifest_sha256, assets: site.records.size,
  isolation: args.includes('--isolation'),
}, null, 2));
const stop = async () => { await preview.close(); process.exit(0); };
process.once('SIGINT', stop);
process.once('SIGTERM', stop);
