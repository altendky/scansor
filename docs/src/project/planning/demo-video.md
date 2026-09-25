# Two-workflow demo video

**Planned, 2026-09-24.** This is a production plan for one concise Scansor
prototype walkthrough. It does not establish product readiness, physical
accuracy, supported input formats, or a public recipe schema.

## Purpose and audience

The video should let a technically interested viewer understand the current
interaction model without requiring a live presenter. It will use two different
examples because they carry complementary evidence:

- the captured nozzle scan shows retained user selection effort and local
  analytic surface fitting on imperfect real-world geometry;
- the generated repeated-boss scan shows feature reuse, generated subtrees,
  exact relationships, reference geometry, calibration, and an applied output
  transform in a controlled example.

The target is an approximately **5:15** silent master at `1920x1080`, accompanied by a
timestamped TTS script and WebVTT or SRT captions. Use Kokoro's `af_heart` voice
for the working voiced edition, pronouncing the project name as “SCAN-sor.”

## Named inputs

The production scripts and narration must use these descriptive checked-in
names, never the old Downloads filenames:

| Segment | Recipe | Source |
| --- | --- | --- |
| Nozzle selection and fitting | [`nozzle-selection-and-fitting-demo.json`](../../../../examples/nozzle-bayonette-simplified/recipes/nozzle-selection-and-fitting-demo.json) | Checked-in captured nozzle example |
| Repeated-boss reuse and alignment | [`repeated-boss-reuse-and-alignment-demo.json`](../../../../examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json) | Generated `scan-coarse` realization |

The boss realization must be regenerated and hash-checked before capture. Both
recipes must validate and complete **Evaluate all** without errors.

## Storyboard

| Time | Picture and interaction | Narration purpose |
| --- | --- | --- |
| 0:00–0:12 | Title: **Scansor — selections, fits, reuse, and alignment** over a clean oblique mesh view | State that this is a working exploratory prototype, not a product or accuracy claim. |
| 0:12–0:38 | Nozzle scan alone with a restrained orbit | Introduce captured, irregular geometry and the evidence layer. |
| 0:38–0:58 | Add `outer`, `recesses`, and three slope selections one at a time | Show retained selections accumulating as reusable feature inputs. |
| 0:58–1:19 | Add `the middle`, `outer cone fit`, and `recess cone fit`, then make a small view adjustment | Build the distinction between observations, datums, and independently recomputable primitives. |
| 1:19–1:43 | Highlight the three repeated recess-slope selections | Explain exact threefold rotational symmetry as an explicit relationship around an axis. |
| 1:43–2:03 | Enable residual colors and pause on the signed scale | Show local signed deviation without describing it as physical error or acceptance. |
| 2:03–2:13 | Transition card: **Reuse and coordinate recovery** | Mark the move to a generated controlled example. |
| 2:13–2:32 | Rotated noisy boss plate with a restrained orbit | Establish the source scan frame and repeated but imperfect features. |
| 2:32–2:54 | Add source selections, its axis, a plane, and a cylinder fit | Partially build the general feature lineage that will be reused. |
| 2:54–3:20 | Expand `Feature reuse`, select it, and enable reuse volumes | Show approximate placement producing target-local selections and fresh local fits. |
| 3:20–3:42 | Inspect equal-radius and parallel-plane relationships, then show residuals | Distinguish independent local fits from optional exact relationships. |
| 3:42–4:07 | Add sphere fits, point datums, directed axis, `Output frame`, and `Output scale` | Explain measurable landmarks, orientation, and multi-reading uniform scale. |
| 4:07–4:30 | Select `Output transform`, switch to Top, and make one small view adjustment | Deliver the visible before/after alignment while preserving source-coordinate fit values. |
| 4:30–4:50 | Open Rhino export and show Transform and units | Show the explicit handoff boundary without implying production CAD integration. |
| 4:50–5:15 | Closing card | Recap retained observations, primitives, relationships, reuse, and explicit output coordinates. |

The cut must not remove rotational symmetry, the nozzle residual view, boss
reuse volumes, or visible transform application. Those moments distinguish the
two workflows and the kinds of geometric intent they demonstrate. The graph
workspace is intentionally omitted from this cut so the available time stays on
the primary modeling workflow.

## Capture staging

Capture the two applications on separate local ports so one continuous browser
automation can move between them without shell windows appearing onscreen. Use a
fresh browser profile, fixed viewport, 100% page zoom, hidden bookmarks and
developer UI, and a deterministic feature-panel split. Do not capture the
desktop, terminal, Downloads directory, or unrelated browser content.

The nozzle segment loads its checked-in recipe directly. The boss segment needs
two staged states:

1. an establishing state in the source scan frame, before the output transform
   is active in the view;
2. the complete checked-in graph with every action evaluated, where selecting
   `Output transform` visibly applies it.

The staging script may change the in-memory graph output pointer for the first
boss shot, but it must not rewrite the checked-in recipe. The visible workflow
must use actual application behavior; no geometry or result may be composited to
fake a successful fit, relationship, or transform.

Use slow eased cursor motion, a visible click indicator, and at least `500 ms`
after tree expansion or camera motion and `900 ms` after meaningful result
changes. Record those interactions as live application clips. Extend the last
frame when narration outlasts a clip; do not loop the cursor or camera motion.
Avoid scrolling while narration asks the viewer to inspect geometry. Camera
presets should do most of the movement; free orbit is reserved for opening views
and one restrained post-transform adjustment.

## Narration and captions

Write the final narration after a dry-run capture establishes real interaction
durations. The script should contain one block per storyboard row with:

- shot ID and time window;
- plain text for TTS;
- optional SSML pronunciation and pause hints;
- the matching caption text;
- the intended on-screen focus.

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

- Both named checked-in recipes validate and evaluate without errors.
- No source selection IDs, fit results, or transforms are fabricated for video.
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
