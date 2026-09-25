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

The target is a **4:30 to 5:00** silent master at `1920x1080`, accompanied by a
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
| 0:12–0:28 | Nozzle scan alone; slow orbit and Fit view | Introduce a captured scan with irregular geometry and retained user-authored surface regions. |
| 0:28–0:58 | Select `outer`, `recesses`, two slope selections, and two bump selections; use multiselect once, then clear | Show that selections are named reusable features rather than temporary click state. |
| 0:58–1:25 | Inspect `the middle`, `outer cone fit`, and `recess cone fit`; briefly show their guides | Explain the separation between observations, reference geometry, and independently recomputable fits. |
| 1:25–1:45 | Enable residual colors and the all-residuals option; pause on the scale legend | Show local signed deviation without describing it as physical error or acceptance. |
| 1:45–2:00 | Switch to Graph view with the dependency lens, select one cone fit, then return to Model | Show the same recipe as dependencies without replacing the feature tree. |
| 2:00–2:10 | Transition card: **Reuse and coordinate recovery** | Mark the move from captured-data selection to a generated controlled example. |
| 2:10–2:30 | Rotated noisy boss plate before application of the output transform | Establish the scan frame and the repeated but imperfect features. |
| 2:30–3:02 | Select and expand `Feature reuse`; expand one generated target group; enable reuse volumes | Show one source feature group producing target-local selections and fresh local fits at three occurrences. |
| 3:02–3:28 | Inspect equal-radius relationships and `plate and upright shoulders parallel`; show all residuals | Explain that reused fits remain local while optional exact relationships connect chosen quantities. |
| 3:28–3:48 | Graph view, relationships lens, selected neighborhood on | Make fit lineage and cross-feature relationships visible together. |
| 3:48–4:12 | Inspect the three sphere fits, two point datums, directed axis, `Output frame`, and `Output scale` | Explain that sphere centers supply measurable points; a directed axis and plate plane define orientation; known point distance determines uniform scale. |
| 4:12–4:32 | Select `Output transform`; let the model visibly move into its aligned output frame; use Top then Fit view | Deliver the principal before/after moment and state that mesh, selections, guides, residual markers, and export share the transform. |
| 4:32–4:48 | Open Rhino export, show the chosen Transform and units, then cancel without downloading | Show the handoff boundary without implying a production CAD integration. |
| 4:48–5:00 | Closing card with the aligned model and Graph view inset | Recap: retain observations, fit primitives, relate or reuse them, and make output coordinates explicit. |

The capture may be shortened toward 4:30 by reducing pauses, but it must not
remove the nozzle residual view, boss reuse volumes, or visible transform
application. Those three moments distinguish the two workflows.

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
changes. Avoid scrolling while narration asks the viewer to inspect geometry.
Camera presets should do most of the movement; free orbit is reserved for the
opening views.

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

The repository-local mise configuration pins FFmpeg, Kokoro, and Kokoro's
spaCy English model. Run
`mise install --locked` from the repository root to install the production tools
alongside the development tools. Kokoro's mise declaration selects CPU-only
PyTorch wheels so production setup does not fetch an unused CUDA toolchain.
The FFmpeg declaration builds the pinned release with `libx264` enabled and
therefore requires the host's FFmpeg build prerequisites and x264 development
library. That produces a GPL-licensed external production executable; it is not
linked into, bundled with, or distributed as part of Scansor.

## Acceptance checklist

- Both named checked-in recipes validate and evaluate without errors.
- No source selection IDs, fit results, or transforms are fabricated for video.
- Text remains readable at `1920x1080` without depending on fullscreen playback.
- Cursor, camera, and tree motion never compete with narration.
- The output-transform application is visible as one continuous UI event.
- Residual colors include their numeric scale and do not imply acceptance.
- Generated and captured examples are identified correctly.
- The silent master, TTS text, and captions have matching shot IDs and timing.
- A second machine can reproduce the capture from the manifest and README.
