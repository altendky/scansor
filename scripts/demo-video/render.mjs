#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
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
const pauseSeconds = 0.55;
const reuseTts = process.env.SCANSOR_DEMO_REUSE_TTS === '1';

const shotMap = new Map([
  ['00', ['00-title']],
  ['01', ['01-nozzle-overview']],
  ['02', ['02-nozzle-selections']],
  ['03', ['03-nozzle-outer-fit', '04-nozzle-fit-pair']],
  ['04', ['05-nozzle-residuals']],
  ['05', ['06-nozzle-graph']],
  ['06', ['07-transition']],
  ['07', ['08-boss-source-frame']],
  ['08', ['09-boss-reuse']],
  ['09', ['10-boss-relationships']],
  ['10', ['11-boss-graph']],
  ['11', ['12-boss-datums', '13-boss-frame', '14-boss-scale']],
  ['12', ['14-boss-scale', '15-boss-transformed', '16-boss-transformed-top', '17-boss-export']],
  ['13', ['18-closing']],
]);

await mkdir(resolve(outputDirectory, 'tts'), { recursive: true });

function command(executable, arguments_, options = {}) {
  return execFileSync(executable, arguments_, {
    cwd: repository,
    encoding: 'utf8',
    stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    maxBuffer: 32 * 1024 * 1024,
  });
}

function mise(tool, arguments_, options = {}) {
  return command('mise', ['exec', '--', tool, ...arguments_], options);
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

async function sha256(path) {
  return createHash('sha256').update(await readFile(path)).digest('hex');
}

const narration = sections(await readFile(narrationPath, 'utf8'));
if (narration.length !== shotMap.size) {
  throw new Error(`Expected ${shotMap.size} narration sections; found ${narration.length}`);
}

for (const section of narration) {
  const textPath = resolve(outputDirectory, 'tts', `${section.id}.txt`);
  const audioPath = resolve(outputDirectory, 'tts', `${section.id}.wav`);
  const ttsText = section.text.replaceAll('Scansor', 'SCAN-sor');
  const utterances = ttsText.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [ttsText];
  await writeFile(textPath, `${utterances.map((item) => item.trim()).join('\n')}\n`);
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

const audioArguments = [];
const audioFilters = [];
for (const [index, section] of narration.entries()) {
  audioArguments.push('-i', section.audioPath);
  const playedDuration = section.duration / playbackRate;
  const padded = playedDuration + pauseSeconds;
  audioFilters.push(
    `[${index}:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,atempo=${playbackRate},apad=pad_dur=${pauseSeconds},atrim=0:${padded.toFixed(6)}[a${index}]`,
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
      chunk,
      '',
    );
    captionCursor = end;
  });

  const sectionTotal = playedDuration + pauseSeconds;
  const shots = shotMap.get(section.id);
  const shotDuration = sectionTotal / shots.length;
  for (const shot of shots) {
    const path = resolve(captureDirectory, `${shot}.png`).replaceAll("'", "'\\''");
    timeline.push(`file '${path}'`, `duration ${shotDuration.toFixed(6)}`);
  }
  timings.push({
    id: section.id,
    title: section.title,
    start: sectionStart,
    end: sectionEnd,
    duration: playedDuration,
    shots,
  });
  cursor += sectionTotal;
}
const finalShot = shotMap.get(narration.at(-1).id).at(-1);
timeline.push(`file '${resolve(captureDirectory, `${finalShot}.png`)}'`);

const captionPath = resolve(outputDirectory, 'scansor-demo-captions.srt');
const timelinePath = resolve(outputDirectory, 'video.ffconcat');
await writeFile(captionPath, `${captions.join('\n')}\n`);
await writeFile(timelinePath, `${timeline.join('\n')}\n`);

const silentVideo = resolve(outputDirectory, 'scansor-demo-silent.mp4');
mise('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-y',
  '-f', 'concat', '-safe', '0', '-i', timelinePath,
  '-t', cursor.toFixed(6),
  '-vf', 'fps=30,format=yuv420p',
  '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
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
const frames = {};
for (const shots of shotMap.values()) {
  for (const shot of shots) {
    const filename = `${shot}.png`;
    frames[filename] = await sha256(resolve(captureDirectory, filename));
  }
}
const manifest = {
  produced_at: new Date().toISOString(),
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
    sample_rate_hz: 24000,
    duration_seconds: probeDuration(narrationAudio),
    words: wordCount,
    words_per_minute: wordsPerMinute * playbackRate,
  },
  source_sha256: sourceHashes,
  frame_sha256: frames,
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

function chromeExecutable() {
  return process.env.SCANSOR_DEMO_CHROME || 'google-chrome';
}

console.log(`Rendered ${timestamp(cursor, '.')} of video to ${outputDirectory}`);
