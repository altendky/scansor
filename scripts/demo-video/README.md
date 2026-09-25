# Demo video production

This directory contains the deterministic browser-driving source for the
two-workflow demo planned in
[`demo-video.md`](../../docs/src/project/planning/demo-video.md). Rendered frames,
speech, and video are intentionally kept outside the repository.

Start the repeated-boss server on port `8765` and the nozzle server on port
`8766`, then capture the review sources:

```sh
node scripts/demo-video/capture.mjs /tmp/scansor-demo/capture
```

The capture uses Chrome's DevTools protocol directly and adds no browser-driving
package to Scansor. Override `SCANSOR_DEMO_CHROME`,
`SCANSOR_DEMO_BOSS_URL`, or `SCANSOR_DEMO_NOZZLE_URL` when needed. The capture
changes only the boss server's in-memory output pointer while staging the
pre-transform shot; it does not modify the checked-in recipe.

The narration source is
[`demo-video-narration.md`](../../docs/src/project/planning/demo-video-narration.md).
Install the project-local Kokoro and FFmpeg production tools separately from the
default development toolchain:

```sh
mise -E demo install --locked
```

Then create the silent master, working voiced edition, captions, and capture
manifest:

```sh
node scripts/demo-video/render.mjs \
  /tmp/scansor-demo/capture \
  /tmp/scansor-demo/render
```

The capture records short application clips with a visible cursor, actual tree
interaction, and restrained camera motion. Title and transition cards remain
still images. The renderer synthesizes one audio file per narration section so
timing and wording remain reviewable, builds sentence-level SRT captions, and
extends each clip's final frame to match its narration rather than looping the
interaction.

Set `SCANSOR_DEMO_REUSE_TTS=1` to reuse already-rendered section WAV files after
changing only assembly settings. The working cut uses a small `1.04` post-process
playback adjustment; `SCANSOR_DEMO_PLAYBACK_RATE` can override it without
resynthesizing the voice.

Kokoro's English phonemizer requires the spaCy `en_core_web_sm` model. It is
declared explicitly in `mise.demo.toml`; relying on Kokoro's runtime
auto-installer can put the model outside the isolated tool environment.
