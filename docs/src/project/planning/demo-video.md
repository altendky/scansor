# Two-workflow demo video

**Production plan, updated 2026-10-08.** This is a concise walkthrough of
Scansor's selection, fitting, relationships, and CAD handoff workflow.

## Purpose and audience

The video should help a prospective user see how to turn scan observations into
a model with cleanly aligned planes and features. Show painting a selection and
choosing geometry directly in the Model view, then explain the result of each
relationship in terms of the part: common centers, square shoulders, parallel
planes, shared surfaces, and matching radii. Use two complementary examples:

- the captured nozzle scan begins bare and focuses on its bayonet mount: create
  representative wall, rim, and repeated locking-feature selections, fit those
  observations through Model picks, and align the surfaces with relationships;
- the generated repeated-boss scan shows reusing selection work, picking fitted
  planes for a relationship, and inspecting aligned surfaces before choosing
  output orientation and CAD export settings.

Keep the narration below **500 words**, with enough time for real interactions
and pauses to inspect their results. Produce a silent master at `1920x1080`, a
working voiced edition, a timestamped script, and SRT captions. Use Kokoro's
`af_heart` voice, pronouncing the project name as “SCAN-sor.” Final timing follows
the dry-run capture and synthesized narration rather than a fixed runtime.

## Named inputs

The production scripts and narration must use these descriptive checked-in
names, never the old Downloads filenames:

| Segment | Recipe | Source |
| --- | --- | --- |
| Nozzle selection and fitting | [`nozzle-selection-and-fitting-demo.json`](../../../../examples/nozzle-bayonette-simplified/recipes/nozzle-selection-and-fitting-demo.json) | Checked-in captured nozzle example |
| Repeated-boss reuse and alignment | [`repeated-boss-reuse-and-alignment-demo.json`](../../../../examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json) | Generated `scan-coarse` realization |

Generate the boss realization in the session's temporary directory and hash-check
it before capture. Both recipes must validate; every fit and relationship shown
must evaluate successfully. The current boss recipe also includes constructed
faces and a Body feature. Verify their actual evaluated state before showing
them or making an assembly claim; a saved Body definition alone does not prove a
closed solid.

## Storyboard

| Shot | Picture and interaction | User-facing purpose |
| --- | --- | --- |
| 00 | Title: **Scansor — clean, aligned geometry** | Introduce the modeling task. |
| 01 | Bare captured nozzle with a restrained orbit | Introduce its bayonet mount before selections and fits exist. |
| 02 | Create outer-wall, top-rim, and one repeated-slope selection through real strokes | Show how useful modeling inputs are chosen. |
| 03 | Pick those selections in Model and create representative cone and plane fits | Turn the actual recorded observations into simple surfaces. |
| 04 | Clearly cut to the remaining prepared fits, create a representative relationship through Model picks, and inspect the common-axis, perpendicular, and threefold results | Show how relationships align the mount's body, rim, and repeated bayonet features without recording every feature's creation. |
| 05 | Enable residual colors with the signed scale visible | Briefly check fitted geometry against the scan. |
| 06 | Transition card: **Repeated features, aligned surfaces** | Identify the generated example. |
| 07 | Rotated boss plate with a restrained orbit | Introduce four bosses, including a taller one. |
| 08 | Show the first boss's cylinder fits, common axis, and shoulder plane | Show a coherent round feature with a square shoulder. |
| 09 | Expand Feature reuse and briefly show reuse volumes | Carry selection effort to other bosses while fitting their own observations. |
| 10 | Choose a parallel-plane relationship, pick its fitted participants in Model, and apply; inspect existing coincident-plane and equal-radius relationships | Explain shared directions, shared surfaces, and matching sizes using visible results. |
| 11 | Inspect aligned shoulder and plate planes, round features, and evaluated constructed faces where available | Show a well-organized model that keeps the taller boss's distinct height. |
| 12 | Apply Output transform continuously, then show an aligned view | Choose useful orientation and scale without losing the scan reference. |
| 13 | Open Export CAD and select an available scope, Transform, and units | Make the CAD handoff choices visible. |
| 14 | Closing card | Recap selecting evidence, fitting surfaces, and connecting them with relationships. |

Retain rotational symmetry, the nozzle residual check, boss reuse volumes, and
visible transform application. Give selection painting and fitted-surface picking
enough screen time to see the pointer, input selection, and resulting geometry.
Keep the Graph panel closed so the emphasis stays on modeling the part.

## Capture staging

Capture the two applications on separate local ports so one continuous browser
automation can move between them without shell windows appearing onscreen. Use a
fresh browser profile, fixed viewport, 100% page zoom, hidden bookmarks and
developer UI, and deterministic Features, Model, and Edit placement. Use the
current dockable toolbar and input-selection controls. Do not capture the
desktop, terminal, Downloads directory, or unrelated browser content.

The nozzle segment starts with only the source node from its checked-in recipe.
Create genuine outer-wall, top-rim, and repeated-slope selections through the UI;
use those recorded memberships in the fits shown immediately afterward. Gather
the wall observations from more than one viewing direction so its cone fit has
useful coverage. The rim selection should stay on the flat face.

Save that filmed-stage recipe separately. Use a clear cut and “With the remaining
surfaces prepared…” to move to the remaining features. Keep the actual recorded
selection memberships and fits in that checkpoint, remapping the prepared
features to use them. Record both checkpoints in the manifest. Do not silently
substitute the saved selections for the recorded ones.

The boss segment needs two staged states:

1. an establishing state in the source scan frame, before the output transform
   is active in the view;
2. the evaluated model, where selecting `Output transform` visibly applies it.

The staging script may change the in-memory graph output pointer for the first
boss shot. Painting, fitting, and relationship creation may add features through
the real UI on these disposable servers. Preserve the checked-in recipes and
record the resulting session recipes with the capture artifacts. No geometry or
result may be composited to fake a successful fit, relationship, or transform.

Find eligible model targets through the application's hover and picking behavior,
then record actual pointer clicks. Show an overlapping-item chooser when the
application needs one. Confirm that picked inputs contain the intended features
before applying the form, and wait for the resulting fit or relationship to
finish evaluating.

Use brisk eased cursor motion and a visible click indicator. Keep painting,
geometry picks, Apply, and their results readable. Shorten navigation, repeated
tree clicks, and idle time when they create gaps after the narration; an overlong
clip should be edited or recaptured rather than automatically retaining all of
its quiet time. Record those interactions as live application clips. Extend the
last frame when narration outlasts a clip; do not loop the cursor or camera motion.
Avoid scrolling while narration asks the viewer to inspect geometry. Camera
presets should do most of the movement; free orbit is reserved for opening views
and one restrained post-transform adjustment.

## Narration and captions

Adjust the working narration after a dry-run capture establishes real interaction
durations. Keep one block per storyboard row with:

- shot ID and time window;
- plain text for TTS;
- optional SSML pronunciation and pause hints;
- the matching caption text;
- the intended on-screen focus.

Preserve section IDs `00` through `14` and their existing capture filenames so
the renderer can assemble the revised shots. No segment may cut off painting,
input picking, applying a form, or the visible result; allow additional quiet
screen time when an interaction outlasts its narration.
Modest acceleration of an overlong recorded clip is allowed to tighten the
pacing, with its rate recorded in the manifest. Keep strokes, input choices,
and results readable; do not imply that the cut measures evaluation speed.
Long evaluation waits may be shortened while retaining the submitted action and
the real evaluated result. Record those omissions in the capture manifest;
do not imply that the edited video demonstrates evaluation speed.

Target `125–140` spoken words per minute and leave approximately `500 ms` of
silence around section transitions. Pronounce **Scansor** consistently as
“SCAN-sor” unless the project owner chooses another pronunciation. Captions
should be sentence-based, use at most two lines, and avoid covering the feature
tree or residual scale. On-screen callouts should be shorter than six words and
should label geometry rather than repeat the narration.

The narration must distinguish:

- captured scan evidence from generated fixture truth;
- a fitted residual from physical or manufacturing error;
- an exact declared relationship from a tolerance check;
- an approximate reuse-placement transform from the fresh local fits it seeds;
- the applied output transform from mutation of upstream fitted values.

## Production artifacts

Keep automation, narration source, and caption source reviewable as text. Rendered
video, frame sequences, synthesized speech, and intermediate media should remain
outside Git unless their size and retention are approved separately. The planned
handoff is:

- silent video master;
- timestamped Markdown TTS script;
- `.srt` or `.vtt` captions;
- optional voiced video after TTS selection;
- capture manifest recording recipe hashes, source hashes, viewport, browser,
  commit, date, and exact commands;
- deterministic browser-driving source and a short rerender README.

## Production tooling

Use an installed FFmpeg executable as an external production program for video
assembly and encoding. It is not a Scansor build, runtime, or package dependency:
do not import or link it, add an FFmpeg wrapper package, bundle its executable,
or distribute it with Scansor. Record the exact command, executable path, version,
build configuration, and codecs in the capture manifest so another producer can
reproduce the media pipeline. FFmpeg builds vary in licensing and codec support;
that variation matters if an executable is later redistributed, but it does not
turn this local production invocation into an application dependency.

Use Kokoro `0.9.4` with the `af_heart` voice as an external production program
for the working narration. Keep its environment and model cache outside the
repository, and record the package, model, voice, sample rate, speed, and exact
invocation in the capture manifest. Generated speech remains a replaceable media
artifact rather than a Scansor build or runtime dependency.

The repository-local `demo` mise environment pins FFmpeg, Kokoro, and Kokoro's
spaCy English model. Run
`mise -E demo install --locked` from the repository root to install the
production tools separately from the default development and CI toolchain.
Kokoro's mise declaration selects CPU-only PyTorch wheels so production setup
does not fetch an unused CUDA toolchain.
The FFmpeg declaration builds the pinned release with `libx264` enabled and
therefore requires the host's FFmpeg build prerequisites and x264 development
library. That produces a GPL-licensed external production executable; it is not
linked into, bundled with, or distributed as part of Scansor.

## Acceptance checklist

- Both named checked-in recipes validate, and all demonstrated geometry and
  relationships evaluate successfully; any assembly failure remains explicit.
- No source selection IDs, fit results, or transforms are fabricated for video.
- Representative selections are visibly created from the bare scan and then
  used by their demonstrated fits.
- The transition to prepared remaining features is clear.
- Navigation and pauses do not leave long quiet tails after the narration.
- A fit input and relationship participants are visibly picked in Model, and
  the recorded form selections match the geometry being discussed.
- Common-axis, perpendicular, parallel, coincident, and equal-radius examples
  show their intended geometric effect without implying unverified solids.
- Text remains readable at `1920x1080` without depending on fullscreen playback.
- Cursor, camera, and tree motion are real application capture and never compete
  with narration.
- The nozzle segment visibly introduces rotational symmetry.
- The graph workspace is not used in this cut.
- The output-transform application is visible as one continuous UI event.
- Residual colors include their numeric scale and do not imply acceptance.
- Generated and captured examples are identified correctly.
- The silent master, TTS text, and captions have matching shot IDs and timing.
- A second machine can reproduce the capture from the manifest and README.
