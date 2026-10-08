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
stages the boss server's in-memory output pointer for the pre-transform shot.
For the nozzle, it maps physical painting targets using the saved example's
hover picker, resets to the bare source, creates wall, rim, and slope selections,
and fits their actual observations through Model picks. It then adds the prepared
remaining features at a visible cut, preserving the filmed selections and fits
as their inputs, and creates a parallel-plane relationship through the real UI.
The filmed stage and completed checkpoint are saved separately in the manifest.
Use disposable servers: these changes stay in their sessions.
The checked-in recipes are preserved. `SCANSOR_DEMO_CDP_PORT` changes
the debugging port; `SCANSOR_DEMO_WORKFLOW=boss` or `nozzle` recaptures just that
workflow into the same capture directory.
`SCANSOR_DEMO_FROM_SHOT=10` retains earlier clips while replaying their staging
interactions and recording from shot 10 onward. Reset the disposable server to
its original recipe before recapturing, to avoid accumulating demo features.
For nozzle shots 4 and 5, it restores the saved filmed stage and prepared
checkpoint, preserving the selections and fits recorded in the earlier clips.
`SCANSOR_DEMO_REUSE_STROKES=1` reuses the saved nozzle stroke plan when recapturing
with the same source mesh, fixed viewport, and docked panel arrangement.

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

The capture records short application clips with a visible cursor, scan painting,
input picks on rendered geometry, tree interaction, and restrained camera motion.
Title and transition cards remain
still images. The renderer synthesizes one audio file per narration section so
timing and wording remain reviewable, builds sentence-level SRT captions, and
extends each clip's final frame to match its narration. When painting or picking
outlasts the speech, it pads the audio with silence so the entire interaction and
result remain visible. Overlong clips receive a modest playback adjustment,
capped at `1.35` by default, to shorten quiet tails after the narration.
`SCANSOR_DEMO_MAX_CLIP_RATE=1` preserves the capture speed. Actual rates are
recorded per shot in the manifest. It also writes a timestamped narration script and records
source mesh metadata and hashes alongside the capture sources.
Long evaluation waits are shortened between the submitted action and its real
completed result. The capture retains two seconds at the start and one at the
end of the wait, and records omitted intervals in each shot's edits JSON and the
render manifest. Pointer gestures and the evaluated result are preserved.
Naming dialogs and brush setup are also omitted from the selection sequence;
their reasons and intervals are recorded alongside the evaluation edits.

Set `SCANSOR_DEMO_REUSE_TTS=1` to reuse already-rendered section WAV files after
changing only assembly settings. The working cut uses a small `1.04` post-process
playback adjustment; `SCANSOR_DEMO_PLAYBACK_RATE` can override it without
resynthesizing the voice.

`SCANSOR_DEMO_TTS_ONLY=1` synthesizes the narration without assembling video.
The renderer also accepts `SCANSOR_DEMO_WORKFLOW=boss` or `nozzle` to assemble a
preview of one workflow from its captures and narration.

Kokoro's English phonemizer requires the spaCy `en_core_web_sm` model. It is
declared explicitly in `mise.demo.toml`; relying on Kokoro's runtime
auto-installer can put the model outside the isolated tool environment.
