#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';

const repository = resolve(import.meta.dirname, '../..');
const captureDirectory = resolve(process.argv[2] || '/tmp/scansor-demo/capture');
const outputDirectory = resolve(process.argv[3] || '/tmp/scansor-demo/render');
const narrationPath = resolve(
  repository,
  'docs/src/project/planning/demo-video-narration.md',
);
const voice = process.env.SCANSOR_DEMO_VOICE || 'af_heart';
const speed = process.env.SCANSOR_DEMO_VOICE_SPEED || '1.0';
const playbackRate = Number(process.env.SCANSOR_DEMO_PLAYBACK_RATE || '1.04');
const maximumClipRate = Number(process.env.SCANSOR_DEMO_MAX_CLIP_RATE || '1.35');
if (!Number.isFinite(maximumClipRate) || maximumClipRate < 1 || maximumClipRate > 2)
  throw new Error('Clip playback limit must be between 1 and 2');
const pauseSeconds = 0.55;
const reuseTts = process.env.SCANSOR_DEMO_REUSE_TTS === '1';
const workflow = process.env.SCANSOR_DEMO_WORKFLOW || 'all';
if (!['all', 'boss', 'nozzle'].includes(workflow)) throw new Error(`Unknown workflow: ${workflow}`);
const commands = [];

const shotMap = new Map([
  ['00', ['00-title']],
  ['01', ['01-nozzle-overview']],
  ['02', ['02-nozzle-selections']],
  ['03', ['03-nozzle-primitives']],
  ['04', ['04-nozzle-rotation']],
  ['05', ['05-nozzle-residuals']],
  ['06', ['06-transition']],
  ['07', ['07-boss-source-frame']],
  ['08', ['08-boss-source-build']],
  ['09', ['09-boss-reuse']],
  ['10', ['10-boss-relationships']],
  ['11', ['11-boss-datums']],
  ['12', ['12-boss-transformed']],
  ['13', ['13-boss-export']],
  ['14', ['14-closing']],
]);

await mkdir(resolve(outputDirectory, 'tts'), { recursive: true });
await mkdir(resolve(outputDirectory, 'segments'), { recursive: true });

function command(executable, arguments_, options = {}) {
  commands.push({ executable, arguments: arguments_ });
  return execFileSync(executable, arguments_, {
    cwd: repository,
    encoding: 'utf8',
    stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    maxBuffer: 32 * 1024 * 1024,
  });
}

function mise(tool, arguments_, options = {}) {
  return command('mise', ['-E', 'demo', 'exec', '--', tool, ...arguments_], options);
}

function sections(markdown) {
  const matches = [...markdown.matchAll(
    /^## (\d\d) — ([^\n]+)[\s\S]*?\*\*Narration:\*\*\n\n([\s\S]*?)(?=\n## |(?![\s\S]))/gm,
  )];
  return matches.map((match) => ({
    id: match[1],
    title: match[2].trim(),
    text: match[3].replace(/\n+/g, ' ').replace(/\s+/g, ' ').trim(),
  }));
}

function probeDuration(path) {
  return Number(mise(
    'ffprobe',
    ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', path],
    { capture: true },
  ).trim());
}

function timestamp(seconds, separator = ',') {
  const milliseconds = Math.round(seconds * 1000);
  const hours = Math.floor(milliseconds / 3600000);
  const minutes = Math.floor((milliseconds % 3600000) / 60000);
  const wholeSeconds = Math.floor((milliseconds % 60000) / 1000);
  const remainder = milliseconds % 1000;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(wholeSeconds).padStart(2, '0')}${separator}${String(remainder).padStart(3, '0')}`;
}

function captionChunks(text, maximum = 84) {
  const sentences = text.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [text];
  const chunks = [];
  for (const sentence of sentences.map((item) => item.trim())) {
    const words = sentence.split(/\s+/);
    let chunk = '';
    for (const word of words) {
      const candidate = chunk ? `${chunk} ${word}` : word;
      if (candidate.length > maximum && chunk) {
        chunks.push(chunk);
        chunk = word;
      } else chunk = candidate;
    }
    if (chunk) chunks.push(chunk);
  }
  return chunks;
}

function captionLines(text) {
  if (text.length <= 42) return text;
  const spaces = [...text.matchAll(/ /g)].map(match => match.index);
  const split = spaces.reduce((best, index) =>
    Math.abs(index - text.length / 2) < Math.abs(best - text.length / 2) ? index : best);
  return `${text.slice(0, split)}\n${text.slice(split + 1)}`;
}

async function sha256(path) {
  return createHash('sha256').update(await readFile(path)).digest('hex');
}

const allNarration = sections(await readFile(narrationPath, 'utf8'));
if (allNarration.length !== shotMap.size) {
  throw new Error(`Expected ${shotMap.size} narration sections; found ${allNarration.length}`);
}
const narration = allNarration.filter(section => workflow === 'all' ||
  (workflow === 'boss' ? Number(section.id) >= 6 : Number(section.id) < 6));
for (const id of shotMap.keys()) if (!narration.some(section => section.id === id)) shotMap.delete(id);

for (const section of narration) {
  console.log(`Narrating ${section.id}: ${section.title}`);
  const textPath = resolve(outputDirectory, 'tts', `${section.id}.txt`);
  const audioPath = resolve(outputDirectory, 'tts', `${section.id}.wav`);
  const ttsText = section.text.replaceAll('Scansor', 'SCAN-sor');
  const utterances = ttsText.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [ttsText];
  const text = `${utterances.map((item) => item.trim()).join('\n')}\n`;
  if (reuseTts && await readFile(textPath, 'utf8') !== text) {
    throw new Error(`Narration ${section.id} changed; regenerate TTS instead of reusing it`);
  }
  await writeFile(textPath, text);
  if (!reuseTts) {
    mise('kokoro', ['-m', voice, '-s', speed, '-i', textPath, '-o', audioPath]);
  }
  section.audioPath = audioPath;
  section.duration = probeDuration(audioPath);
}

const wordCount = narration.reduce(
  (count, section) => count + section.text.split(/\s+/).filter(Boolean).length,
  0,
);
const speechSeconds = narration.reduce((total, section) => total + section.duration, 0);
const wordsPerMinute = wordCount / (speechSeconds / 60);
if (wordsPerMinute > 180 || wordsPerMinute < 90) {
  throw new Error(
    `Implausible narration rate ${wordsPerMinute.toFixed(1)} words/minute; check TTS output for truncation`,
  );
}
if (process.env.SCANSOR_DEMO_TTS_ONLY === '1') {
  console.log(`Synthesized ${wordCount} words at ${wordsPerMinute.toFixed(1)} words/minute`);
  process.exit(0);
}

const audioArguments = [];
const audioFilters = [];
// Keep Apply and the completed result. Modest motion acceleration reduces quiet
// tails after speech; longer interactions still receive enough time to finish.
const clipRates = new Map();
for (const section of narration) {
  const spoken = section.duration / playbackRate;
  section.total = Math.max(spoken + pauseSeconds,
    ...shotMap.get(section.id).map(shot => {
      const clip = resolve(captureDirectory, `${shot}.mp4`);
      if (!existsSync(clip)) return 0;
      const duration = probeDuration(clip);
      const rate = Math.min(maximumClipRate, Math.max(1, duration / (spoken + 1)));
      clipRates.set(shot, rate);
      return duration / rate + pauseSeconds;
    }));
}
for (const [index, section] of narration.entries()) {
  audioArguments.push('-i', section.audioPath);
  const padded = section.total;
  audioFilters.push(
    `[${index}:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,atempo=${playbackRate},apad,atrim=0:${padded.toFixed(6)}[a${index}]`,
  );
}
audioFilters.push(
  `${narration.map((_, index) => `[a${index}]`).join('')}concat=n=${narration.length}:v=0:a=1[out]`,
);
const narrationAudio = resolve(outputDirectory, 'scansor-demo-narration.wav');
mise('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-y',
  ...audioArguments,
  '-filter_complex', audioFilters.join(';'),
  '-map', '[out]',
  '-c:a', 'pcm_s16le',
  narrationAudio,
]);

let cursor = 0;
let captionNumber = 1;
const captions = [];
const timeline = ['ffconcat version 1.0'];
const timings = [];
let shotNumber = 0;
for (const section of narration) {
  const sectionStart = cursor;
  const playedDuration = section.duration / playbackRate;
  const sectionEnd = cursor + playedDuration;
  const chunks = captionChunks(section.text);
  const weights = chunks.map((chunk) => Math.max(1, chunk.length));
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
  let captionCursor = sectionStart;
  chunks.forEach((chunk, index) => {
    const end = index === chunks.length - 1
      ? sectionEnd
      : captionCursor + playedDuration * weights[index] / totalWeight;
    captions.push(
      String(captionNumber++),
      `${timestamp(captionCursor)} --> ${timestamp(end)}`,
      captionLines(chunk),
      '',
    );
    captionCursor = end;
  });

  console.log(`Assembling ${section.id}: ${section.title}`);
  const sectionTotal = section.total;
  const shots = shotMap.get(section.id);
  const shotDuration = sectionTotal / shots.length;
  for (const shot of shots) {
    const clip = resolve(captureDirectory, `${shot}.mp4`);
    const still = resolve(captureDirectory, `${shot}.png`);
    const source = existsSync(clip) ? clip : still;
    if (!existsSync(source)) throw new Error(`Missing capture source for ${shot}`);
    const segment = resolve(
      outputDirectory,
      'segments',
      `${String(shotNumber++).padStart(3, '0')}-${shot}.mp4`,
    );
    const arguments_ = ['-hide_banner', '-loglevel', 'error', '-y'];
    if (source === still) arguments_.push('-loop', '1', '-framerate', '30');
    arguments_.push('-i', source);
    const filter = source === clip
      ? `setpts=(PTS-STARTPTS)/${clipRates.get(shot)},tpad=stop_mode=clone:stop_duration=${shotDuration.toFixed(6)},fps=30,format=yuv420p`
      : 'fps=30,format=yuv420p';
    arguments_.push(
      '-t', shotDuration.toFixed(6), '-vf', filter, '-an', '-c:v', 'libx264',
      '-preset', 'medium', '-crf', '18', '-movflags', '+faststart', segment,
    );
    mise('ffmpeg', arguments_);
    timeline.push(`file '${segment.replaceAll("'", "'\\''")}'`);
  }
  timings.push({
    id: section.id,
    title: section.title,
    start: sectionStart,
    end: sectionEnd,
    duration: playedDuration,
    video_end: sectionStart + sectionTotal,
    shots,
    clip_playback_rates: Object.fromEntries(shots.filter(shot => clipRates.has(shot)).map(shot => [shot,clipRates.get(shot)])),
  });
  cursor += sectionTotal;
}

const captionPath = resolve(outputDirectory, 'scansor-demo-captions.srt');
const timelinePath = resolve(outputDirectory, 'video.ffconcat');
await writeFile(captionPath, `${captions.join('\n')}\n`);
await writeFile(timelinePath, `${timeline.join('\n')}\n`);

const silentVideo = resolve(outputDirectory, 'scansor-demo-silent.mp4');
mise('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-y',
  '-f', 'concat', '-safe', '0', '-i', timelinePath,
  '-t', cursor.toFixed(6),
  '-c:v', 'copy',
  '-movflags', '+faststart',
  silentVideo,
]);

const voicedVideo = resolve(outputDirectory, 'scansor-demo-voiced.mp4');
mise('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-y',
  '-i', silentVideo, '-i', narrationAudio,
  '-map', '0:v:0', '-map', '1:a:0',
  '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k',
  '-shortest', '-movflags', '+faststart',
  voicedVideo,
]);

const recipePaths = [
  'examples/nozzle-bayonette-simplified/recipes/nozzle-selection-and-fitting-demo.json',
  'examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json',
];
const sourceHashes = {};
for (const path of recipePaths) sourceHashes[path] = await sha256(resolve(repository, path));
for (const path of ['scripts/demo-video/capture.mjs', 'scripts/demo-video/render.mjs',
  'docs/src/project/planning/demo-video-narration.md', 'src/scansor/nonlinear_least_squares.py']) {
  sourceHashes[path] = await sha256(resolve(repository, path));
}
const meshes = {};
const sessions = {};
const checkpoints = {};
for (const example of workflow === 'all' ? ['nozzle', 'boss'] : [workflow]) {
  meshes[example] = JSON.parse(await readFile(resolve(captureDirectory, `${example}-source.json`), 'utf8'));
  const path = resolve(captureDirectory, `${example}-session.json`);
  sessions[example] = { recipe: JSON.parse(await readFile(path, 'utf8')), sha256: await sha256(path) };
}
if (workflow !== 'boss') {
  for (const name of ['nozzle-filmed-stage', 'nozzle-prepared-checkpoint']) {
    const path = resolve(captureDirectory, `${name}.json`);
    checkpoints[name] = {recipe:JSON.parse(await readFile(path, 'utf8')), sha256:await sha256(path)};
  }
}
const frames = {};
const edits = {};
for (const shots of shotMap.values()) {
  for (const shot of shots) {
    const extension = existsSync(resolve(captureDirectory, `${shot}.mp4`)) ? 'mp4' : 'png';
    const filename = `${shot}.${extension}`;
    frames[filename] = await sha256(resolve(captureDirectory, filename));
    const editPath = resolve(captureDirectory, `${shot}-edits.json`);
    if (existsSync(editPath)) edits[shot] = JSON.parse(await readFile(editPath, 'utf8'));
  }
}
const manifest = {
  produced_at: new Date().toISOString(),
  workflow,
  git_commit: command('git', ['rev-parse', 'HEAD'], { capture: true }).trim(),
  viewport: { width: 1920, height: 1080, device_scale_factor: 1 },
  chrome: command(chromeExecutable(), ['--version'], { capture: true }).trim(),
  ffmpeg: mise('ffmpeg', ['-hide_banner', '-version'], { capture: true }).split('\n').slice(0, 4),
  narration: {
    engine: 'kokoro',
    version: '0.9.4',
    voice,
    synthesis_speed: Number(speed),
    playback_rate: playbackRate,
    reused_audio: reuseTts,
    sample_rate_hz: 24000,
    duration_seconds: probeDuration(narrationAudio),
    words: wordCount,
    words_per_minute: wordsPerMinute * playbackRate,
  },
  source_sha256: sourceHashes,
  source_meshes: meshes,
  captured_sessions: sessions,
  captured_checkpoints: checkpoints,
  commands,
  frame_sha256: frames,
  capture_edits: edits,
  maximum_clip_playback_rate: maximumClipRate,
  timings,
  outputs: {
    silent_video: { path: basename(silentVideo), sha256: await sha256(silentVideo) },
    voiced_video: { path: basename(voicedVideo), sha256: await sha256(voicedVideo) },
    captions: { path: basename(captionPath), sha256: await sha256(captionPath) },
  },
};
await writeFile(
  resolve(outputDirectory, 'capture-manifest.json'),
  `${JSON.stringify(manifest, null, 2)}\n`,
);
await writeFile(resolve(outputDirectory, 'scansor-demo-timed-narration.md'),
  '# Demo narration with video timings\n\n' + narration.map((section, index) =>
    `## ${section.id} — ${section.title}\n\n${timestamp(timings[index].start, '.')} – ${timestamp(timings[index].video_end, '.')}\n\n${section.text}\n`).join('\n'));

function chromeExecutable() {
  return process.env.SCANSOR_DEMO_CHROME || 'google-chrome';
}

console.log(`Rendered ${timestamp(cursor, '.')} of video to ${outputDirectory}`);
