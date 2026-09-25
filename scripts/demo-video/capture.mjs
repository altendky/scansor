#!/usr/bin/env node

import { execFileSync, spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const outputDirectory = resolve(process.argv[2] || '/tmp/scansor-demo-capture');
const chrome = process.env.SCANSOR_DEMO_CHROME || 'google-chrome';
const debuggingPort = Number(process.env.SCANSOR_DEMO_CDP_PORT || 9223);
const bossUrl = process.env.SCANSOR_DEMO_BOSS_URL || 'http://127.0.0.1:8765/';
const nozzleUrl = process.env.SCANSOR_DEMO_NOZZLE_URL || 'http://127.0.0.1:8766/';
const profile = resolve(outputDirectory, 'chrome-profile');

function command(executable, arguments_) {
  return execFileSync(executable, arguments_, {
    cwd: resolve(import.meta.dirname, '../..'),
    encoding: 'utf8',
    stdio: 'inherit',
    maxBuffer: 32 * 1024 * 1024,
  });
}

function mise(tool, arguments_) {
  return command('mise', ['-E', 'demo', 'exec', '--', tool, ...arguments_]);
}

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
    this.listeners = new Map();
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (pending) {
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
        return;
      }
      for (const listener of this.listeners.get(message.method) || []) {
        listener(message.params);
      }
    });
  }

  send(method, params = {}) {
    const id = ++this.sequence;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolvePromise, reject) => {
      this.pending.set(id, { resolve: resolvePromise, reject });
    });
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || new Set();
    listeners.add(listener);
    this.listeners.set(method, listeners);
    return () => listeners.delete(listener);
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
    await installDemoCursor();
  }

  async function screenshot(name) {
    const { data } = await cdp.send('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: false,
      fromSurface: true,
    });
    await writeFile(resolve(outputDirectory, `${name}.png`), Buffer.from(data, 'base64'));
  }

  async function installDemoCursor(x = 1560, y = 140) {
    await evaluate(`(() => {
      document.querySelector('#scansor-demo-cursor')?.remove();
      const cursor = document.createElement('div');
      cursor.id = 'scansor-demo-cursor';
      Object.assign(cursor.style, {
        position: 'fixed', left: '0', top: '0', width: '18px', height: '18px',
        border: '3px solid #f4fbff', borderRadius: '50%', background: '#58d4eb99',
        boxShadow: '0 1px 5px #000b', pointerEvents: 'none', zIndex: '2147483647',
        transform: 'translate(${x - 9}px, ${y - 9}px)',
      });
      document.body.append(cursor);
    })()`);
  }

  async function moveCursorToPoint(x, y, duration = 650) {
    await evaluate(`new Promise((resolvePromise) => {
      const cursor = document.querySelector('#scansor-demo-cursor');
      const animation = cursor.animate(
        [{transform: cursor.style.transform}, {transform: 'translate(${x - 9}px, ${y - 9}px)'}],
        {duration: ${duration}, easing: 'cubic-bezier(.22,.75,.28,1)', fill: 'forwards'},
      );
      animation.onfinish = () => {
        cursor.style.transform = 'translate(${x - 9}px, ${y - 9}px)';
        animation.cancel();
        resolvePromise();
      };
    })`);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  }

  async function elementPoint(expression) {
    const point = await evaluate(`(() => {
      const element = ${expression};
      if (!element) return null;
      element.scrollIntoView({block: 'center', inline: 'nearest'});
      const rectangle = element.getBoundingClientRect();
      return {x: rectangle.left + rectangle.width / 2, y: rectangle.top + rectangle.height / 2};
    })()`);
    if (!point) throw new Error(`Missing element: ${expression}`);
    return point;
  }

  async function pulseCursor() {
    await evaluate(`new Promise((resolvePromise) => {
      const cursor = document.querySelector('#scansor-demo-cursor');
      const animation = cursor.animate(
        [{transform: cursor.style.transform, background: '#58d4eb99'},
         {transform: cursor.style.transform + ' scale(.55)', background: '#fff'},
         {transform: cursor.style.transform, background: '#58d4eb99'}],
        {duration: 260, easing: 'ease-out'},
      );
      animation.onfinish = resolvePromise;
    })`);
  }

  async function clickPoint(x, y) {
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1,
    });
    await sleep(90);
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1,
    });
    await pulseCursor();
  }

  async function click(selector) {
    const point = await elementPoint(`document.querySelector(${JSON.stringify(selector)})`);
    await moveCursorToPoint(point.x, point.y);
    await clickPoint(point.x, point.y);
    await sleep(500);
  }

  async function action(id) {
    const quoted = JSON.stringify(id);
    const expression = `(() => {
      const element = [...document.querySelectorAll('.action-select')]
        .find((candidate) => candidate.dataset.actionId === ${quoted})
        || [...document.querySelectorAll('summary[data-action-id]')]
          .find((candidate) => candidate.dataset.actionId === ${quoted});
      return element?.classList.contains('action-select')
        ? element : element?.querySelector('.group-action');
    })()`;
    const point = await elementPoint(expression);
    await moveCursorToPoint(point.x, point.y);
    await clickPoint(point.x, point.y);
    await sleep(500);
  }

  async function orbit(deltaX, deltaY, duration = 1600) {
    const box = await evaluate(`(() => {
      const rectangle = document.querySelector('#viewport canvas').getBoundingClientRect();
      return {left: rectangle.left, top: rectangle.top, width: rectangle.width, height: rectangle.height};
    })()`);
    const start = {
      x: box.left + box.width * 0.58,
      y: box.top + box.height * 0.52,
    };
    await moveCursorToPoint(start.x, start.y, 600);
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed', ...start, button: 'right', buttons: 2, clickCount: 1,
    });
    const steps = Math.max(12, Math.round(duration / 50));
    for (let step = 1; step <= steps; step++) {
      const progress = step / steps;
      const eased = 0.5 - Math.cos(Math.PI * progress) / 2;
      const x = start.x + deltaX * eased;
      const y = start.y + deltaY * eased;
      await evaluate(`document.querySelector('#scansor-demo-cursor').style.transform =
        'translate(${x - 9}px, ${y - 9}px)'`);
      await cdp.send('Input.dispatchMouseEvent', {
        type: 'mouseMoved', x, y, button: 'right', buttons: 2,
      });
      await sleep(duration / steps);
    }
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: start.x + deltaX, y: start.y + deltaY,
      button: 'right', buttons: 0, clickCount: 1,
    });
    await sleep(500);
  }

  async function recordClip(name, choreography, tailMilliseconds = 700) {
    const framesDirectory = resolve(outputDirectory, `${name}-frames`);
    await mkdir(framesDirectory, { recursive: true });
    const frames = [];
    const writes = [];
    let count = 0;
    const removeListener = cdp.on('Page.screencastFrame', (params) => {
      const filename = resolve(framesDirectory, `${String(count++).padStart(5, '0')}.jpg`);
      frames.push({ filename, timestamp: params.metadata.timestamp });
      writes.push(writeFile(filename, Buffer.from(params.data, 'base64')));
      void cdp.send('Page.screencastFrameAck', { sessionId: params.sessionId });
    });
    await cdp.send('Page.startScreencast', {
      format: 'jpeg', quality: 88, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1,
    });
    await sleep(350);
    await choreography();
    await sleep(tailMilliseconds);
    await cdp.send('Page.stopScreencast');
    removeListener();
    await Promise.all(writes);
    if (!frames.length) throw new Error(`No screencast frames captured for ${name}`);
    const timeline = ['ffconcat version 1.0'];
    for (let index = 0; index < frames.length; index++) {
      const duration = index + 1 < frames.length
        ? Math.max(1 / 60, frames[index + 1].timestamp - frames[index].timestamp)
        : tailMilliseconds / 1000;
      timeline.push(`file '${frames[index].filename}'`, `duration ${duration.toFixed(6)}`);
    }
    timeline.push(`file '${frames.at(-1).filename}'`);
    const timelinePath = resolve(framesDirectory, 'frames.ffconcat');
    await writeFile(timelinePath, `${timeline.join('\n')}\n`);
    mise('ffmpeg', [
      '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0',
      '-i', timelinePath, '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264',
      '-preset', 'medium', '-crf', '18', '-movflags', '+faststart',
      resolve(outputDirectory, `${name}.mp4`),
    ]);
  }

  async function clearSelection() {
    await click('#clear-feature-selection');
  }

  async function setChecked(selector, checked) {
    const quoted = JSON.stringify(selector);
    const point = await elementPoint(`document.querySelector(${quoted})`);
    await moveCursorToPoint(point.x, point.y);
    await evaluate(`(() => {
      const element = document.querySelector(${quoted});
      if (!element) throw new Error('Missing checkbox');
      if (element.checked !== ${Boolean(checked)}) element.click();
    })()`);
    await pulseCursor();
    await sleep(500);
  }

  async function setSelect(selector, value) {
    const quotedSelector = JSON.stringify(selector);
    const quotedValue = JSON.stringify(value);
    const point = await elementPoint(`document.querySelector(${quotedSelector})`);
    await moveCursorToPoint(point.x, point.y);
    await evaluate(`(() => {
      const element = document.querySelector(${quotedSelector});
      if (!element) throw new Error('Missing select');
      element.value = ${quotedValue};
      element.dispatchEvent(new Event('change', {bubbles: true}));
    })()`);
    await pulseCursor();
    await sleep(500);
  }

  async function toggleActionGroup(id) {
    const quoted = JSON.stringify(id);
    const expression = `[...document.querySelectorAll('summary[data-action-id]')]
      .find((candidate) => candidate.dataset.actionId === ${quoted})`;
    const point = await elementPoint(expression);
    point.x -= 26;
    await moveCursorToPoint(point.x, point.y);
    await clickPoint(point.x, point.y);
    await sleep(500);
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
  await recordClip('01-nozzle-overview', async () => {
    await orbit(115, -32, 1800);
  });

  await clearSelection();
  await recordClip('02-nozzle-selections', async () => {
    for (const id of [
      'selection_0350f17f14784ce2b686fafc6e709dfb',
      'selection_2df7d374a3234b18b2d5d96d66844bd0',
      'selection_f1a351baca5946d4978db72fb86a3547',
      'selection_ee2660f02f9348f28ec1511d55d3199d',
      'selection_fd74c02433ae431daf73ac2836b0ce85',
    ]) await action(id);
  }, 1000);

  await clearSelection();
  await recordClip('03-nozzle-primitives', async () => {
    await action('axis_9f2337754ec943caa49bda2723476651');
    await action('fit_e5c4d25f49d04c8497460964929f3581');
    await action('fit_132bdb8984964b8d830cbc9fab21bcd5');
    await orbit(-70, 20, 1100);
  }, 1000);

  await clearSelection();
  await recordClip('04-nozzle-rotation', async () => {
    for (const id of [
      'selection_f1a351baca5946d4978db72fb86a3547',
      'selection_ee2660f02f9348f28ec1511d55d3199d',
      'selection_fd74c02433ae431daf73ac2836b0ce85',
    ]) await action(id);
    await orbit(58, 8, 900);
  }, 900);

  await clearSelection();
  await action('fit_132bdb8984964b8d830cbc9fab21bcd5');
  await recordClip('05-nozzle-residuals', async () => {
    await click('.display-options > summary');
    await setSelect('#colors', 'residual');
  }, 1300);

  await titleCard(
    'Workflow two',
    'Reuse and coordinate recovery',
    'The generated fixture makes local fitting, declared relationships, scale, and orientation easy to inspect.',
    '06-transition',
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
  await installDemoCursor();
  await evaluate("document.querySelector('.display-options').open = true");
  await setChecked('#all-guides', false);
  await evaluate("document.querySelector('.display-options').open = false");
  await clearSelection();
  await action('scan');
  await click('#home');
  await recordClip('07-boss-source-frame', async () => {
    await orbit(105, -24, 1700);
  });

  await clearSelection();
  await recordClip('08-boss-source-build', async () => {
    for (const id of [
      'boss-a-outer',
      'boss-a-shoulder',
      'axis_b970d98ea84144dcbcda2d9331e79ab3',
      'reference_plane_45bb0164b0ef40afbaaf2277268a6a4a',
      'fit_6854e8ef6490471eac2deb01287f0ec4',
    ]) await action(id);
  }, 1000);

  await clearSelection();
  await recordClip('09-boss-reuse', async () => {
    await toggleActionGroup('feature_reuse_boss_d');
    await action('feature_reuse_boss_d');
    await click('.display-options > summary');
    await setChecked('#reuse-volumes', true);
  }, 1400);

  await clearSelection();
  await recordClip('10-boss-relationships', async () => {
    await action('equal_radii_b1d585508886418780432add88f336ff');
    await action('plate-shoulders-parallel');
    await setSelect('#colors', 'residual');
  }, 1300);

  await setSelect('#colors', 'selection');
  await setChecked('#reuse-volumes', false);
  await clearSelection();
  await recordClip('11-boss-datums', async () => {
    for (const id of [
      'fit_0cbed9d8010446f4995c2e113a1657bc',
      'fit_65d210cb937547d6b2f17f1f72359a28',
      'fit_5ca04b3b11c4479c90a166d7cd5e925c',
      'point_3d09ffdfa6094409aef9b4658519c46a',
      'point_5c28c53c5821496f962cd07b5a5441e7',
      'axis_ae49a9cc6b8748f492161e3f5a8551c8',
      'frame_output',
      'scale_output',
    ]) await action(id);
  }, 1200);

  await clearSelection();
  await recordClip('12-boss-transformed', async () => {
    await action('transform_955b213e0ba74ab8a5200bbc99c3c7d0');
    await click('#top');
    await orbit(42, 5, 800);
  }, 1300);

  await recordClip('13-boss-export', async () => {
    await click('#export-rhino');
    await waitFor("document.querySelector('#export-dialog')?.open");
    await setSelect('#export-transform', 'transform_955b213e0ba74ab8a5200bbc99c3c7d0');
    await setSelect('#export-units', 'Millimeters');
  }, 1400);

  await click('[data-close-dialog="export-dialog"]');
  await titleCard(
    'Retain · fit · relate · reuse · orient',
    'Geometry that stays inspectable',
    'Selections remain evidence; primitives and relationships remain explicit; output coordinates remain a separate choice.',
    '14-closing',
  );
} finally {
  if (cdp) await cdp.send('Browser.close').catch(() => {});
  browser.kill('SIGTERM');
}
