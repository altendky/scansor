#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const outputDirectory = resolve(process.argv[2] || '/tmp/scansor-demo-capture');
const chrome = process.env.SCANSOR_DEMO_CHROME || 'google-chrome';
const debuggingPort = Number(process.env.SCANSOR_DEMO_CDP_PORT || 9223);
const bossUrl = process.env.SCANSOR_DEMO_BOSS_URL || 'http://127.0.0.1:8765/';
const nozzleUrl = process.env.SCANSOR_DEMO_NOZZLE_URL || 'http://127.0.0.1:8766/';
const profile = resolve(outputDirectory, 'chrome-profile');

await mkdir(outputDirectory, { recursive: true });

const browser = spawn(
  chrome,
  [
    '--headless=new',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-default-apps',
    '--disable-features=Translate',
    '--enable-unsafe-swiftshader',
    '--enable-webgl',
    '--force-device-scale-factor=1',
    '--hide-scrollbars',
    '--no-first-run',
    '--no-default-browser-check',
    '--remote-allow-origins=*',
    `--remote-debugging-port=${debuggingPort}`,
    `--user-data-dir=${profile}`,
    '--window-size=1920,1080',
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'inherit'] },
);

const sleep = (milliseconds) => new Promise((accept) => setTimeout(accept, milliseconds));

async function retry(operation, timeout = 30000) {
  const deadline = Date.now() + timeout;
  let latest;
  while (Date.now() < deadline) {
    try {
      return await operation();
    } catch (error) {
      latest = error;
      await sleep(200);
    }
  }
  throw latest || new Error('Timed out');
}

class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.sequence = 0;
    this.pending = new Map();
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    });
  }

  send(method, params = {}) {
    const id = ++this.sequence;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolvePromise, reject) => {
      this.pending.set(id, { resolve: resolvePromise, reject });
    });
  }
}

let cdp;
try {
  const target = await retry(async () => {
    const response = await fetch(`http://127.0.0.1:${debuggingPort}/json/list`);
    if (!response.ok) throw new Error(`Chrome target list returned ${response.status}`);
    const targets = await response.json();
    const page = targets.find((candidate) => candidate.type === 'page');
    if (!page) throw new Error('Chrome has no page target yet');
    return page;
  });
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolvePromise, reject) => {
    socket.addEventListener('open', resolvePromise, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  cdp = new Cdp(socket);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1920,
    height: 1080,
    deviceScaleFactor: 1,
    mobile: false,
  });

  async function evaluate(expression) {
    const response = await cdp.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (response.exceptionDetails) {
      throw new Error(response.exceptionDetails.exception?.description || 'Browser evaluation failed');
    }
    return response.result.value;
  }

  async function waitFor(expression, timeout = 120000) {
    await retry(async () => {
      if (!(await evaluate(expression))) throw new Error(`Waiting for: ${expression}`);
    }, timeout);
  }

  async function navigate(url) {
    await cdp.send('Page.navigate', { url });
    await waitFor("document.readyState === 'complete'", 30000);
    await waitFor("document.querySelector('#status')?.textContent.includes('All actions evaluated')");
    await sleep(1000);
  }

  async function screenshot(name) {
    const { data } = await cdp.send('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: false,
      fromSurface: true,
    });
    await writeFile(resolve(outputDirectory, `${name}.png`), Buffer.from(data, 'base64'));
  }

  async function click(selector) {
    const quoted = JSON.stringify(selector);
    const found = await evaluate(`(() => {
      const element = document.querySelector(${quoted});
      if (!element) return false;
      element.scrollIntoView({block: 'nearest'});
      element.click();
      return true;
    })()`);
    if (!found) throw new Error(`Missing element: ${selector}`);
    await sleep(700);
  }

  async function action(id) {
    const quoted = JSON.stringify(id);
    const found = await evaluate(`(() => {
      const element = [...document.querySelectorAll('.action-select')]
        .find((candidate) => candidate.dataset.actionId === ${quoted})
        || [...document.querySelectorAll('summary[data-action-id]')]
          .find((candidate) => candidate.dataset.actionId === ${quoted});
      if (!element) return false;
      element.scrollIntoView({block: 'center'});
      if (element.classList.contains('action-select')) element.click();
      else element.querySelector('.group-action')?.click();
      return true;
    })()`);
    if (!found) throw new Error(`Missing action: ${id}`);
    await sleep(700);
  }

  async function clearSelection() {
    await click('#clear-feature-selection');
  }

  async function setChecked(selector, checked) {
    const quoted = JSON.stringify(selector);
    await evaluate(`(() => {
      const element = document.querySelector(${quoted});
      if (!element) throw new Error('Missing checkbox');
      if (element.checked !== ${Boolean(checked)}) element.click();
    })()`);
    await sleep(700);
  }

  async function setSelect(selector, value) {
    const quotedSelector = JSON.stringify(selector);
    const quotedValue = JSON.stringify(value);
    await evaluate(`(() => {
      const element = document.querySelector(${quotedSelector});
      if (!element) throw new Error('Missing select');
      element.value = ${quotedValue};
      element.dispatchEvent(new Event('change', {bubbles: true}));
    })()`);
    await sleep(700);
  }

  async function titleCard(kicker, title, subtitle, name) {
    const html = `<!doctype html><meta charset="utf-8"><style>
      html,body{margin:0;width:100%;height:100%;background:#101923;color:#e8f1f8;font-family:system-ui,sans-serif}
      body{display:grid;place-items:center}.card{width:1320px}.k{color:#7ddff2;font-size:27px;letter-spacing:.18em;text-transform:uppercase}
      h1{font-size:76px;line-height:1.06;margin:28px 0 30px;max-width:1250px}p{font-size:32px;line-height:1.4;color:#aebdcc;max-width:1120px}
      .rule{width:160px;height:7px;background:#69d6e8;margin-top:52px;border-radius:5px}
    </style><main class="card"><div class="k">${kicker}</div><h1>${title}</h1><p>${subtitle}</p><div class="rule"></div></main>`;
    await cdp.send('Page.navigate', { url: `data:text/html;charset=utf-8,${encodeURIComponent(html)}` });
    await waitFor("document.readyState === 'complete'", 10000);
    await screenshot(name);
  }

  await titleCard(
    'Exploratory prototype',
    'Scansor',
    'Retain selections, fit analytic primitives, reuse observations, and make output coordinates explicit.',
    '00-title',
  );

  await navigate(nozzleUrl);
  await evaluate("document.querySelector('.display-options').open = true");
  await setChecked('#all-guides', false);
  await evaluate("document.querySelector('.display-options').open = false");
  await clearSelection();
  await action('scan');
  await click('#home');
  await screenshot('01-nozzle-overview');

  await clearSelection();
  for (const id of [
    'selection_0350f17f14784ce2b686fafc6e709dfb',
    'selection_2df7d374a3234b18b2d5d96d66844bd0',
    'selection_ee2660f02f9348f28ec1511d55d3199d',
    'selection_58c41f91f2564984a3290e9ec2971806',
  ]) await action(id);
  await screenshot('02-nozzle-selections');

  await clearSelection();
  await action('fit_e5c4d25f49d04c8497460964929f3581');
  await screenshot('03-nozzle-outer-fit');
  await action('fit_132bdb8984964b8d830cbc9fab21bcd5');
  await screenshot('04-nozzle-fit-pair');

  await click('.display-options > summary');
  await setSelect('#colors', 'residual');
  await screenshot('05-nozzle-residuals');

  await clearSelection();
  await action('fit_132bdb8984964b8d830cbc9fab21bcd5');
  await click('#graph-view-tab');
  await setSelect('#feature-graph-lens', 'dependencies');
  await screenshot('06-nozzle-graph');

  await titleCard(
    'Workflow two',
    'Reuse and coordinate recovery',
    'The generated fixture makes local fitting, declared relationships, scale, and orientation easy to inspect.',
    '07-transition',
  );

  await navigate(bossUrl);
  await evaluate(`(async () => {
    const state = await fetch('/api/graph').then((response) => response.json());
    state.recipe.output = 'scale_output';
    const response = await fetch('/api/graph', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Scansor-Request': '1'},
      body: JSON.stringify({token: state.token, recipe: state.recipe}),
    });
    if (!response.ok) throw new Error(await response.text());
    location.reload();
  })()`);
  await waitFor("document.readyState === 'complete'", 30000);
  await waitFor("document.querySelector('#status')?.textContent.includes('All actions evaluated')");
  await clearSelection();
  await action('scan');
  await click('#home');
  await screenshot('08-boss-source-frame');

  await evaluate(`(() => {
    const summary = document.querySelector('[data-action-id="feature_reuse_boss_d"]');
    if (!summary) return false;
    summary.closest('details').open = true;
    const target = summary.closest('details').querySelector('details.managed-target');
    if (target) target.open = true;
    summary.scrollIntoView({block: 'center'});
    summary.querySelector('.group-action')?.click();
    return true;
  })()`);
  await click('#model-view-tab');
  await evaluate("document.querySelector('.display-options').open = true");
  await setChecked('#reuse-volumes', true);
  await screenshot('09-boss-reuse');

  await clearSelection();
  await action('equal_radii_b1d585508886418780432add88f336ff');
  await action('plate-shoulders-parallel');
  await setSelect('#colors', 'residual');
  await screenshot('10-boss-relationships');

  await click('#graph-view-tab');
  await setSelect('#feature-graph-lens', 'relationships');
  await setChecked('#feature-graph-neighborhood', false);
  await screenshot('11-boss-graph');

  await click('#model-view-tab');
  await setSelect('#colors', 'selection');
  await setChecked('#reuse-volumes', false);
  await clearSelection();
  for (const id of [
    'fit_0cbed9d8010446f4995c2e113a1657bc',
    'fit_65d210cb937547d6b2f17f1f72359a28',
    'fit_5ca04b3b11c4479c90a166d7cd5e925c',
    'point_3d09ffdfa6094409aef9b4658519c46a',
    'point_5c28c53c5821496f962cd07b5a5441e7',
    'axis_ae49a9cc6b8748f492161e3f5a8551c8',
  ]) await action(id);
  await screenshot('12-boss-datums');

  await clearSelection();
  await action('frame_output');
  await screenshot('13-boss-frame');
  await clearSelection();
  await action('scale_output');
  await screenshot('14-boss-scale');

  await clearSelection();
  await action('transform_955b213e0ba74ab8a5200bbc99c3c7d0');
  await screenshot('15-boss-transformed');
  await click('#top');
  await screenshot('16-boss-transformed-top');

  await click('#export-rhino');
  await waitFor("document.querySelector('#export-dialog')?.open");
  await setSelect('#export-transform', 'transform_955b213e0ba74ab8a5200bbc99c3c7d0');
  await setSelect('#export-units', 'Millimeters');
  await screenshot('17-boss-export');

  await click('[data-close-dialog="export-dialog"]');
  await click('#graph-view-tab');
  await setSelect('#feature-graph-lens', 'combined');
  await setChecked('#feature-graph-neighborhood', true);
  await screenshot('18-closing');
} finally {
  if (cdp) await cdp.send('Browser.close').catch(() => {});
  browser.kill('SIGTERM');
}
