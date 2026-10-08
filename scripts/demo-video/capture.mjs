#!/usr/bin/env node

import { execFileSync, spawn } from 'node:child_process';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const outputDirectory = resolve(process.argv[2] || '/tmp/scansor-demo-capture');
const chrome = process.env.SCANSOR_DEMO_CHROME || 'google-chrome';
const debuggingPort = Number(process.env.SCANSOR_DEMO_CDP_PORT || 9223);
const bossUrl = process.env.SCANSOR_DEMO_BOSS_URL || 'http://127.0.0.1:8765/';
const nozzleUrl = process.env.SCANSOR_DEMO_NOZZLE_URL || 'http://127.0.0.1:8766/';
const workflow = process.env.SCANSOR_DEMO_WORKFLOW || 'all';
if (!['all', 'boss', 'nozzle'].includes(workflow)) throw new Error(`Unknown workflow: ${workflow}`);
const firstShot = Number(process.env.SCANSOR_DEMO_FROM_SHOT || 0);
if (!Number.isInteger(firstShot) || firstShot < 0 || firstShot > 14) throw new Error('Invalid starting shot');
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
await rm(profile, { recursive: true, force: true });

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
let evaluationWaits = null;
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
    const metadata = await evaluate("fetch('/api/meta').then(response => response.json())");
    await writeFile(resolve(outputDirectory, url === bossUrl ? 'boss-source.json' : 'nozzle-source.json'), `${JSON.stringify(metadata, null, 2)}\n`);
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

  async function moveCursorToPoint(x, y, duration = 280, buttons = 0) {
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
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, buttons,
      ...(buttons ? {button: 'left'} : {}) });
  }

  async function elementPoint(expression) {
    const point = await evaluate(`(() => {
      const element = ${expression};
      if (!element) return null;
      element.scrollIntoView({block: 'center', inline: 'nearest'});
      const rectangle = element.getBoundingClientRect();
      if (!rectangle.width || !rectangle.height) throw new Error('Element is not visible');
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
        {duration: 130, easing: 'ease-out'},
      );
      animation.onfinish = resolvePromise;
    })`);
  }

  async function clickPoint(x, y) {
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1,
    });
    await sleep(55);
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1,
    });
    await pulseCursor();
  }

  async function click(selector) {
    const proxy = /^#[\w-]+$/.test(selector) ? `[data-command-id="${selector.slice(1)}"]` : selector;
    const point = await elementPoint(`document.querySelector(${JSON.stringify(proxy)}) || document.querySelector(${JSON.stringify(selector)})`);
    await moveCursorToPoint(point.x, point.y);
    await clickPoint(point.x, point.y);
    await sleep(180);
  }

  async function action(id) {
    const quoted = JSON.stringify(id);
    const expression = `(() => {
      const element = [...document.querySelectorAll('.action-select')]
        .find((candidate) => candidate.dataset.actionId === ${quoted})
        || [...document.querySelectorAll('summary[data-action-id]')]
          .find((candidate) => candidate.dataset.actionId === ${quoted});
      for (let parent = element?.parentElement; parent; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') parent.open = true;
      }
      return element?.classList.contains('action-select')
        ? element : element?.querySelector('.action-name');
    })()`;
    const point = await elementPoint(expression);
    await moveCursorToPoint(point.x, point.y);
    await clickPoint(point.x, point.y);
    await sleep(250);
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
    await moveCursorToPoint(start.x, start.y, 280);
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
    if (Number(name.slice(0, 2)) < firstShot) {
      await choreography();
      return;
    }
    console.log(`Capturing ${name}`);
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
    evaluationWaits = [];
    await sleep(350);
    await choreography();
    await sleep(tailMilliseconds);
    await cdp.send('Page.stopScreencast');
    removeListener();
    await Promise.all(writes);
    await screenshot(name);
    if (!frames.length) throw new Error(`No screencast frames captured for ${name}`);
    const timeline = ['ffconcat version 1.0'];
    const edits = evaluationWaits;
    evaluationWaits = null;
    for (let index = 0; index < frames.length; index++) {
      const start = frames[index].timestamp;
      const end = index + 1 < frames.length ? frames[index + 1].timestamp : start + tailMilliseconds / 1000;
      let duration = end - start;
      for (const edit of edits) duration -= Math.max(0, Math.min(end, edit.end) - Math.max(start, edit.start));
      if (duration > 0) timeline.push(`file '${frames[index].filename}'`, `duration ${Math.max(1 / 60, duration).toFixed(6)}`);
    }
    timeline.push(`file '${frames.at(-1).filename}'`);
    const timelinePath = resolve(framesDirectory, 'frames.ffconcat');
    await writeFile(timelinePath, `${timeline.join('\n')}\n`);
    await writeFile(resolve(outputDirectory, `${name}-edits.json`), `${JSON.stringify({
      omitted_intervals: edits.map(edit => ({
        reason:edit.reason || 'evaluation_wait',
        start: edit.start - frames[0].timestamp, end: edit.end - frames[0].timestamp,
      })),
    }, null, 2)}\n`);
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

  async function closeEditPanel() {
    const point = await elementPoint(`[...document.querySelectorAll('[role="tab"]')]
      .find(tab => tab.textContent.startsWith('Edit'))?.querySelector('[title="Close"]')`);
    await moveCursorToPoint(point.x, point.y);
    await clickPoint(point.x, point.y);
    await sleep(500);
  }

  async function dockEdit() {
    if (await evaluate("document.querySelector('#edit-popup').open")) await click('#dock-edit');
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
    if (await evaluate(`document.querySelector(${quotedSelector})?.value === ${quotedValue}`)) return;
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
      .find((candidate) => candidate.dataset.actionId === ${quoted})?.querySelector('.tree-toggle')`;
    const point = await elementPoint(expression);
    await moveCursorToPoint(point.x, point.y);
    await clickPoint(point.x, point.y);
    await sleep(500);
  }

  async function fill(selector, value) {
    await click(selector);
    await evaluate(`(() => {
      const input = document.querySelector(${JSON.stringify(selector)});
      input.value = ${JSON.stringify(value)};
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
    })()`);
  }

  async function featureContext(id) {
    const point = await elementPoint(`[...document.querySelectorAll('.action-select, summary[data-action-id]')].find(element => element.dataset.actionId === ${JSON.stringify(id)})`);
    await moveCursorToPoint(point.x, point.y);
    await cdp.send('Input.dispatchMouseEvent', {type: 'mousePressed', ...point, button: 'right', buttons: 2, clickCount: 1});
    await cdp.send('Input.dispatchMouseEvent', {type: 'mouseReleased', ...point, button: 'right', buttons: 0, clickCount: 1});
    await sleep(300);
  }

  // Locate patches through the application's public hover/raycast interaction.
  // Only the eventual CDP mouse click commits a pick; no feature IDs are injected.
  async function patch(label, clearance = 0) {
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const point = await evaluate(`(() => {
      const canvas = document.querySelector('#viewport canvas'), box = canvas.getBoundingClientRect();
      const hit = (x, y) => {
        if (document.elementFromPoint(x, y) !== canvas) return false;
        canvas.dispatchEvent(new PointerEvent('pointermove', {clientX: x, clientY: y, pointerId: 1}));
        const message = document.querySelector('#model-pick-message').textContent.trim();
        return message === ${JSON.stringify(label)} || message.startsWith(${JSON.stringify(`${label} — `)});
      };
      const step = ${clearance} ? 8 : 14;
      for (let y = box.top + 85; y < box.bottom - 55; y += step) {
        for (let x = box.left + 10; x < box.right - 10; x += step) {
          if (hit(x, y) && (!${clearance} || [[${clearance},0],[-${clearance},0],[0,${clearance}],[0,-${clearance}]]
            .every(([dx,dy]) => hit(x + dx, y + dy)))) return {x, y};
        }
      }
      return null;
    })()`);
    if (!point) throw new Error(`No visible model patch: ${label}`);
    return point;
  }

  async function pickPatch(label, checkedSelector, hint) {
    const point = hint || await patch(label);
    await moveCursorToPoint(point.x, point.y);
    await sleep(500);
    await clickPoint(point.x, point.y);
    const choice = await evaluate(`(() => {
      const button = [...document.querySelectorAll('#model-pick-choices button')]
        .find(button => button.textContent.includes(${JSON.stringify(label)}));
      if (!button || !button.getBoundingClientRect().width) return null;
      const box = button.getBoundingClientRect();
      return {x: box.left + box.width/2, y: box.top + box.height/2};
    })()`);
    if (choice) {
      await moveCursorToPoint(choice.x, choice.y);
      await clickPoint(choice.x, choice.y);
    }
    if (checkedSelector) await waitFor(`document.querySelector(${JSON.stringify(checkedSelector)})?.checked`);
    await sleep(500);
    return point;
  }

  async function evaluated() {
    const waitStart = Date.now() / 1000;
    await waitFor(`(async () => {
      const graph = await fetch('/api/graph').then(response => response.json());
      return !graph.evaluation_running && Object.values(graph.states).every(state => state === 'ready');
    })()`);
    const graph = await evaluate("fetch('/api/graph').then(response => response.json())");
    const failures = Object.entries(graph.states).filter(([,state]) => state !== 'ready');
    if (failures.length) throw new Error(`Failed demo features: ${JSON.stringify(failures)}`);
    const waitEnd = Date.now() / 1000;
    if (evaluationWaits && waitEnd - waitStart > 6) {
      evaluationWaits.push({start: waitStart + 2, end: waitEnd - 1});
    }
    return graph;
  }

  async function replaceRecipe(recipe) {
    await evaluate(`(async () => {
      const state = await fetch('/api/graph').then(response => response.json());
      const response = await fetch('/api/graph', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Scansor-Request': '1'},
        body: JSON.stringify({token: state.token, recipe: ${JSON.stringify(recipe)}}),
      });
      if (!response.ok) throw new Error(await response.text());
    })()`);
    await navigate(nozzleUrl);
  }

  async function selectionView(view) {
    await click(view === 'top' ? '#top' : '#side');
    if (view === 'other-side') await orbit(240, 0, 450);
  }

  // Find physical regions before filming. Hover only locates strokes: the
  // subsequent real left-button gestures determine every recorded membership.
  async function strokePaths(label, diameter, maximum, view = 'side') {
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const paths = await evaluate(`(async () => {
      const canvas = document.querySelector('#viewport canvas'), box = canvas.getBoundingClientRect();
      const step = ${diameter} <= 4 ? 2 : ${diameter} <= 8 ? 4 : 8, radius = ${diameter} / 2;
      let left=box.left+10, right=box.right-10, top=box.top+85, bottom=box.bottom-55;
      if (${JSON.stringify(view)} !== 'other-side') {
        // Public source coordinates narrow the search; the application's actual
        // hover result and brush clearance still qualify every planned stroke.
        const THREE = await import('three');
        const metadata = await fetch('/api/meta').then(response => response.json());
        const positions = new Float32Array(await fetch(metadata.positions.url).then(response => response.arrayBuffer()));
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position',new THREE.BufferAttribute(positions,3)); geometry.computeBoundingSphere();
        const sphere=geometry.boundingSphere, camera=new THREE.PerspectiveCamera(45,box.width/box.height,Math.max(.001,sphere.radius/1000),sphere.radius*1000);
        camera.up.set(0,0,1);
        const distance=sphere.radius/Math.sin(Math.min(camera.fov*Math.PI/360,Math.atan(Math.tan(camera.fov*Math.PI/360)*camera.aspect)))*1.12;
        const direction=${JSON.stringify(view)} === 'top' ? new THREE.Vector3(0,-.001,1) : new THREE.Vector3(0,-1,0);
        camera.position.copy(sphere.center).addScaledVector(direction.normalize(),distance); camera.lookAt(sphere.center); camera.updateMatrixWorld();
        const graph=await fetch('/api/graph').then(response => response.json());
        const ids=graph.recipe.nodes.find(node => node.label===${JSON.stringify(label)}).ids;
        const projected=ids.map(id => new THREE.Vector3().fromArray(positions,id*3).project(camera));
        const xs=projected.map(p => box.left+(p.x+1)*box.width/2), ys=projected.map(p => box.top+(1-p.y)*box.height/2);
        left=Math.max(left,Math.min(...xs)-16); right=Math.min(right,Math.max(...xs)+16);
        top=Math.max(top,Math.min(...ys)-16); bottom=Math.min(bottom,Math.max(...ys)+16);
        geometry.dispose();
      }
      const hit = (x, y) => {
        if (document.elementFromPoint(x, y) !== canvas) return false;
        canvas.dispatchEvent(new PointerEvent('pointermove', {clientX:x, clientY:y, pointerId:1}));
        const message = document.querySelector('#model-pick-message').textContent.trim();
        return message === ${JSON.stringify(label)} || message.startsWith(${JSON.stringify(`${label} — `)});
      };
      const points = [];
      for (let y = top; y < bottom; y += step)
        for (let x = left; x < right; x += step)
          if (hit(x,y) && [[radius,0],[-radius,0],[0,radius],[0,-radius]].every(([dx,dy]) => hit(x+dx,y+dy)))
            points.push({x,y});
      const candidates = [];
      for (const [fixed, variable, diagonal] of [['y','x',0],['x','y',0],['y','x',1],['y','x',-1]]) {
        const groups = new Map();
        for (const point of points) {
          const key=diagonal ? Math.round((point.y-diagonal*point.x)/step) : point[fixed];
          const group = groups.get(key) || [];
          group.push(point); groups.set(key,group);
        }
        for (const group of groups.values()) {
          group.sort((a,b) => a[variable]-b[variable]);
          let run = [];
          for (const point of group) {
            if (run.length && point[variable]-run.at(-1)[variable] > step+0.1) {
              if (run.length >= 3) candidates.push(run);
              run = [];
            }
            run.push(point);
          }
          if (run.length >= 3) candidates.push(run);
        }
      }
      candidates.sort((a,b) => b.length-a.length);
      const chosen = [];
      for (const path of candidates) {
        const center = path[Math.floor(path.length/2)];
        if (chosen.every(other => {
          const middle = other[Math.floor(other.length/2)];
          return Math.hypot(center.x-middle.x,center.y-middle.y) > ${diameter}*2;
        })) chosen.push(path);
        if (chosen.length === ${maximum}) break;
      }
      return chosen;
    })()`);
    if (!paths.length) throw new Error(`No useful painting stroke for ${label}`);
    console.log(`Mapped ${label}: ${paths.map(path => path.length).join(',')} points`);
    return paths;
  }

  async function paintPaths(paths, diameter) {
    await omitSetup('brush_setup', async () => {
      await setSelect('#selection-shape', 'paint');
      await fill('#brush-size', String(diameter));
    });
    for (const path of paths) {
      await moveCursorToPoint(path[0].x, path[0].y);
      await cdp.send('Input.dispatchMouseEvent', {type:'mousePressed', ...path[0], button:'left', buttons:1, clickCount:1});
      // The normal brush sweeps the full segment between pointer events.
      // Fewer evenly spaced events keep this straight stroke brisk.
      const moves=path.slice(1).filter((_,index) => index%4===3);
      if (moves.at(-1) !== path.at(-1)) moves.push(path.at(-1));
      for (const point of moves) await moveCursorToPoint(point.x, point.y, 45, 1);
      await cdp.send('Input.dispatchMouseEvent', {type:'mouseReleased', ...path.at(-1), button:'left', buttons:0, clickCount:1});
      await waitFor("!document.querySelector('#evaluate-all').disabled");
      await evaluated();
    }
  }

  async function createSelection(label) {
    await click('#add-selection');
    await waitFor("!document.querySelector('#selection-tools').hidden");
    const id = await evaluate("fetch('/api/graph').then(response => response.json()).then(graph => graph.recipe.nodes.at(-1).id)");
    await omitSetup('selection_naming', async () => {
      await featureContext(id);
      await click('#feature-context-rename');
      await fill('#rename-feature-label', label);
      await click('#rename-feature-form button');
    });
    return id;
  }

  async function omitSetup(reason, operation) {
    const start=Date.now()/1000;
    await operation();
    if (evaluationWaits) evaluationWaits.push({start,end:Date.now()/1000,reason});
  }

  async function createFit(label, selection, kind, hint) {
    await click('#new-fit');
    await setSelect('#new-fit-kind', kind);
    await fill('#new-fit-label', label);
    await click('[data-pick-control="new-fit-inputs"]');
    await pickPatch(selection, undefined, hint);
    await waitFor(`[...document.querySelector('#new-fit-inputs').selectedOptions].some(option => option.textContent === ${JSON.stringify(selection)})`);
    await click('#stop-model-picking');
    await click('#add-fit-form > button');
    const state = await evaluated();
    const fit = state.recipe.nodes.find(node => node.label === label);
    if (state.states[fit?.id] !== 'ready') throw new Error(`Fit not ready: ${label}`);
    console.log(`Created ${label}: RMS ${state.results[fit.id].weighted_rms}`);
    return fit.id;
  }

  async function projectedSelection(label, view = 'top') {
    return evaluate(`(async () => {
      const THREE=await import('three'), metadata=await fetch('/api/meta').then(response => response.json());
      const positions=new Float32Array(await fetch(metadata.positions.url).then(response => response.arrayBuffer()));
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.computeBoundingSphere();
      const sphere=geometry.boundingSphere,box=document.querySelector('#viewport canvas').getBoundingClientRect();
      const camera=new THREE.PerspectiveCamera(45,box.width/box.height,Math.max(.001,sphere.radius/1000),sphere.radius*1000);camera.up.set(0,0,1);
      const distance=sphere.radius/Math.sin(Math.min(camera.fov*Math.PI/360,Math.atan(Math.tan(camera.fov*Math.PI/360)*camera.aspect)))*1.12;
      const direction=${JSON.stringify(view)} === 'top' ? new THREE.Vector3(0,-.001,1) : new THREE.Vector3(0,-1,0);
      camera.position.copy(sphere.center).addScaledVector(direction.normalize(),distance);camera.lookAt(sphere.center);camera.updateMatrixWorld();
      const graph=await fetch('/api/graph').then(response => response.json()),ids=graph.recipe.nodes.find(node=>node.label===${JSON.stringify(label)}).ids;
      const points=ids.filter((_,index)=>index%4===0).map(id=>{
        const p=new THREE.Vector3().fromArray(positions,id*3).project(camera);
        return {x:box.left+(p.x+1)*box.width/2,y:box.top+(1-p.y)*box.height/2};
      });geometry.dispose();return points;
    })()`);
  }

  async function locateRelationshipPick(label, id, candidates) {
    for (const point of candidates) {
      await moveCursorToPoint(point.x,point.y);
      await clickPoint(point.x,point.y);
      const found=await evaluate(`(() => {
        const popup=document.querySelector('#model-pick-choices');
        const match=popup.matches(':popover-open') && [...popup.querySelectorAll('button')].some(button=>button.textContent.includes(${JSON.stringify(label)}));
        const selected=document.querySelector('input[data-participant-id="${id}"]')?.checked;
        if(popup.matches(':popover-open'))popup.hidePopover();
        return match||selected;
      })()`);
      if(found)return point;
      await click('#clear-relationship-participants');
    }
    throw new Error(`No selectable relationship surface: ${label}`);
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

  if (workflow !== 'boss') {
  await titleCard(
    'Scan to model',
    'Scansor',
    'From a noisy scan to surfaces that line up, share a center, and describe the part you want to model.',
    '00-title',
  );

  await navigate(nozzleUrl);
  const nozzleRecipe = await evaluate("fetch('/api/graph/example').then(response => response.json())");
  const filmed = {};
  let filmedStage, completedRecipe, topPaths;
  if (firstShot < 4) {
  await evaluate("document.querySelector('.display-options').open = true");
  await setChecked('#all-guides', false);
  await evaluate("document.querySelector('.display-options').open = false");
  await clearSelection();
  await click('#new-fit');
  await dockEdit();
  await click('[data-pick-control="new-fit-inputs"]');
  let strokePlan;
  if (process.env.SCANSOR_DEMO_REUSE_STROKES === '1') {
    strokePlan = JSON.parse(await readFile(resolve(outputDirectory, 'nozzle-stroke-plan.json'), 'utf8'));
  } else {
    await selectionView('side');
    const outerPaths = await strokePaths('outer', 12, 3);
    const slopePaths = await strokePaths('recess slope a', 4, 1);
    await selectionView('other-side');
    const otherOuterPaths = await strokePaths('outer', 12, 2, 'other-side');
    await selectionView('top');
    const topPaths = await strokePaths('top', 12, 4, 'top');
    strokePlan = {outerPaths,slopePaths,otherOuterPaths,topPaths};
    await writeFile(resolve(outputDirectory, 'nozzle-stroke-plan.json'), `${JSON.stringify(strokePlan, null, 2)}\n`);
  }
  const {outerPaths,slopePaths,otherOuterPaths} = strokePlan;
  topPaths = strokePlan.topPaths;
  await click('#stop-model-picking');
  await click('[data-close-dialog="fit-dialog"]');
  const source = nozzleRecipe.nodes.find(node => node.operation === 'source');
  await replaceRecipe({schema_version:2, nodes:[source], output:source.id});
  await clearSelection();
  await action('scan');
  await selectionView('side');
  await recordClip('01-nozzle-overview', async () => {
    await orbit(85, -24, 700);
  });

  await recordClip('02-nozzle-selections', async () => {
    filmed.outer = await createSelection('Outside wall');
    await selectionView('side');
    await paintPaths(outerPaths, 12);
    await selectionView('other-side');
    await paintPaths(otherOuterPaths, 12);
    await setSelect('#tool', 'orbit');
    filmed.top = await createSelection('Top rim');
    await selectionView('top');
    await paintPaths(topPaths, 12);
    await setSelect('#tool', 'orbit');
    filmed.slope = await createSelection('Repeated slope');
    await selectionView('side');
    await paintPaths(slopePaths, 4);
    await setSelect('#tool', 'orbit');
    const graph = await evaluated();
    for (const id of Object.values(filmed)) {
      const selection = graph.recipe.nodes.find(node => node.id === id);
      if (selection.ids.length < (id === filmed.slope ? 8 : 30)) throw new Error(`Insufficient observations: ${selection.label}`);
      console.log(`Painted ${selection.label}: ${selection.ids.length} vertices`);
    }
  }, 500);

  await clearSelection();
  await recordClip('03-nozzle-primitives', async () => {
    await selectionView('side');
    filmed.outerFit = await createFit('Outside wall fit', 'Outside wall', 'cone', outerPaths[0][0]);
    await selectionView('top');
    filmed.topFit = await createFit('Top rim fit', 'Top rim', 'plane', topPaths[0][0]);
  }, 500);

  filmedStage = (await evaluated()).recipe;
  await writeFile(resolve(outputDirectory, 'nozzle-filmed-stage.json'), `${JSON.stringify(filmedStage, null, 2)}\n`);
  // Add prepared remaining features at the visible cut, retaining the exact
  // selections and fits created on screen as inputs to the completed model.
  const replacements = new Map([
    ['selection_0350f17f14784ce2b686fafc6e709dfb', filmed.outer],
    ['selection_0bcf6d9c18b84ed59026a3c906600409', filmed.top],
    ['selection_f1a351baca5946d4978db72fb86a3547', filmed.slope],
    ['fit_f8cd508ae1074528875fb135cb1576a3', filmed.outerFit],
    ['fit_e61afa8a605d4e78bae9e36ad47f5a9d', filmed.topFit],
  ]);
  const remap = value => typeof value === 'string' ? replacements.get(value) || value
    : Array.isArray(value) ? value.map(remap)
      : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([key,item]) => [key,remap(item)])) : value;
  completedRecipe = remap({...nozzleRecipe, nodes:nozzleRecipe.nodes.map(node =>
    replacements.has(node.id) ? filmedStage.nodes.find(filmedNode => filmedNode.id === replacements.get(node.id)) : node)});
  } else {
    filmedStage = JSON.parse(await readFile(resolve(outputDirectory, 'nozzle-filmed-stage.json'), 'utf8'));
    completedRecipe = JSON.parse(await readFile(resolve(outputDirectory, 'nozzle-prepared-checkpoint.json'), 'utf8'));
    filmed.outerFit = filmedStage.nodes.find(node=>node.label==='Outside wall fit').id;
    filmed.topFit = filmedStage.nodes.find(node=>node.label==='Top rim fit').id;
    topPaths = JSON.parse(await readFile(resolve(outputDirectory, 'nozzle-stroke-plan.json'), 'utf8')).topPaths;
  }
  await replaceRecipe(completedRecipe);
  await evaluated();
  await writeFile(resolve(outputDirectory, 'nozzle-prepared-checkpoint.json'), `${JSON.stringify(completedRecipe, null, 2)}\n`);

  await clearSelection();
  await selectionView('top');
  await click('#new-relationship');
  await dockEdit();
  await setSelect('#relationship-kind', 'parallel_planes');
  await click('#pick-relationship-participant');
  const topFitPoint = await locateRelationshipPick('Top rim fit', filmed.topFit, topPaths.map(path=>path[Math.floor(path.length/2)]));
  await selectionView('side');
  const recessFitPoint = await locateRelationshipPick('recesses axial', 'fit_114f6f1a367d4f2f839457ad67db3dfc', await projectedSelection('recess axial', 'side'));
  await click('#stop-model-picking');
  await click('#clear-relationship-participants');
  await setSelect('#relationship-kind', '');
  await selectionView('top');
  await recordClip('04-nozzle-rotation', async () => {
    await setSelect('#relationship-kind', 'parallel_planes');
    await click('#pick-relationship-participant');
    await pickPatch('Top rim fit', `input[data-participant-id="${filmed.topFit}"]`, topFitPoint);
    await selectionView('side');
    await pickPatch('recesses axial', 'input[data-participant-id="fit_114f6f1a367d4f2f839457ad67db3dfc"]', recessFitPoint);
    await click('#stop-model-picking');
    await click('#add-relationship');
    await evaluated();
    await action('coaxial_e9d9e36158714115b1d5b7afd639ae3b');
    await action('perpendicular_45f4ef03bd994f4cb6af915c1b3de636');
    await action('rotation_aa53aee84e9e4659ba636c9057465b1b');
    await orbit(58, -24, 550);
  }, 500);

  await clearSelection();
  await action(filmed.outerFit);
  await recordClip('05-nozzle-residuals', async () => {
    await click('.display-options > summary');
    await setSelect('#colors', 'residual');
  }, 1300);
  await writeFile(resolve(outputDirectory, 'nozzle-session.json'),
    `${JSON.stringify((await evaluated()).recipe, null, 2)}\n`);
  }

  if (workflow !== 'nozzle') {
  await titleCard(
    'Workflow two',
    'Make the surfaces work together',
    'Repeated bosses, matching diameters, and shoulders that stay cleanly aligned.',
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
  await click('#home');
  // Find the two eligible patches before recording, so the audience sees only
  // the deliberate picks rather than the production script's raycast search.
  await click('.display-options > summary');
  await click('#new-relationship');
  await dockEdit();
  await setSelect('#relationship-kind', 'parallel_planes');
  await click('#pick-relationship-participant');
  const platePoint = await patch('plate top fit');
  const bossShoulderPoint = await patch('shoulder fit');
  await click('#stop-model-picking');
  await setSelect('#relationship-kind', '');
  await recordClip('10-boss-relationships', async () => {
    await setSelect('#relationship-kind', 'parallel_planes');
    await fill('#new-relationship-label', 'Aligned shoulders');
    await click('#pick-relationship-participant');
    await pickPatch('plate top fit', 'input[data-participant-id="plate-top-fit"]', platePoint);
    await pickPatch('shoulder fit', 'input[data-participant-id="fit_56e9d6f9a2a344629c52cba6931add0f"]', bossShoulderPoint);
    await click('#stop-model-picking');
    await click('#add-relationship');
    await evaluated();
    await clearSelection();
    await action('equal_radii_b1d585508886418780432add88f336ff');
    await action('plate-shoulders-parallel');
    await action('plane_relationship_13e06a91c7a14bdba8c03e1b7972b96c');
  }, 1300);

  await setSelect('#colors', 'selection');
  await setChecked('#reuse-volumes', false);
  await clearSelection();
  await closeEditPanel();
  await recordClip('11-boss-datums', async () => {
    await action('body_a79f8eb306ad466b8f4526aa763957d2');
    await setChecked('#guides', false);
    await setChecked('#faces-only', true);
    await click('.display-options > summary');
    await orbit(55, -12, 1300);
  }, 1200);

  await clearSelection();
  await recordClip('12-boss-transformed', async () => {
    await action('transform_955b213e0ba74ab8a5200bbc99c3c7d0');
    await click('#top');
    await orbit(42, 5, 800);
  }, 1300);

  await recordClip('13-boss-export', async () => {
    await click('#export-cad');
    await waitFor("document.querySelector('#export-dialog')?.open");
    await setSelect('#export-scope', 'body');
    await setSelect('#export-transform', 'transform_955b213e0ba74ab8a5200bbc99c3c7d0');
    await setSelect('#export-units', 'Millimeters');
  }, 1400);

  await click('[data-close-dialog="export-dialog"]');
  await writeFile(resolve(outputDirectory, 'boss-session.json'),
    `${JSON.stringify((await evaluated()).recipe, null, 2)}\n`);
  await titleCard(
    'Select · fit · align',
    'A model whose features belong together',
    'Choose the scan regions. Describe their relationships. Keep the surfaces cleanly aligned.',
    '14-closing',
  );
  }
} finally {
  if (cdp) await cdp.send('Browser.close').catch(() => {});
  browser.kill('SIGTERM');
}
