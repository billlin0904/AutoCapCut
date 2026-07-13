# AutoCapCut MVP

Local desktop wrapper around `video-autopilot-kit`.

## Layout

```text
F:\Source\AutoCapCut
  autocapcut_app\        # Our app code
  video-autopilot-kit\   # Upstream repo, tracked as a git submodule
  launch_pyside.ps1      # GUI launcher
```

## Launch GUI

After cloning this repo, initialize the upstream dependency:

```powershell
git submodule update --init --recursive
```

From PowerShell:

```powershell
F:\Source\AutoCapCut\launch_pyside.ps1
```

Or run directly:

```powershell
C:\Users\User\anaconda3\Scripts\conda.exe run -n AutoCapCut python -m autocapcut_app
```

## Current MVP

The first screen is a short-video renderer:

- Import a Segment Plan from the top toolbar or the `File` menu.
- Show source segments and caption editing in the left editor panel.
- Open render settings from the `File` menu.
- Edit caption blocks and colored text segments in GUI tables.
- Choose BGM start behavior: auto highlight, start at 0s, or manual seconds.
- Preview main/address caption Y positions in the center stage.
- Scrub and play from the bottom timeline; imported source segments are drawn as
  rounded blocks.
- Render a 1080x1920 MP4 using `video-autopilot-kit`.
- Show render output in a dialog when `Start Render` is pressed.

The desktop UI uses `PySide6-Fluent-Widgets` for a light Fluent-style editing
workspace on top of PySide6.

This is a workflow shell, not yet an auto-editor. The user still chooses clips,
durations, caption timing, and BGM.

The preview panel decodes the selected timeline frame, crops it to the 9:16
canvas, then overlays the caption text and Y-position guides. It uses PyAV for
fast scrubbing and falls back to ffmpeg if needed. It is a frame layout preview,
not a full video player. Preview frames are cached under:

```text
C:\Users\User\.autocapcut\preview_cache
```

The timeline below the preview is draggable. It maps the output timeline to the
configured clip segments and extracts a still frame for the selected time.
Caption start/end ranges appear as markers on the timeline:

- yellow: `main`
- blue: `addr`

The preview controls below the timeline provide start/end jumps, caption marker
navigation, one-second stepping, and frame-scrub playback. While playing, the app
also plays the source clip audio for the current timeline position. Clips without
an audio track will preview silently.

## Segment Plan Import

Use `Import Segment Plan` to load JSON produced by an AI highlight picker. Every
entry in `segments` is treated as a selected source clip and appended to the
current short-video timeline. Captions inside each segment are shifted forward by
the accumulated duration of the previous imported segments.

Minimum shape:

```json
{
  "source_video": "F:/Videos/science_long.mp4",
  "segments": [
    {
      "id": "seg_001",
      "source_start": 124.2,
      "source_end": 161.8,
      "title": "Why time feels faster",
      "hook_score": 9,
      "caption_blocks": [
        {
          "start": 0.0,
          "end": 2.8,
          "kind": "main",
          "segments": [["為什麼長大後", "w"], ["時間變快", "y"]]
        }
      ]
    }
  ]
}
```

Notes:

- `source_start` / `source_end` can be seconds or timecodes like `00:02:04.2`.
- Caption `start` / `end` should usually be relative to that segment.
- If caption times are accidentally absolute source-video times, the importer
  converts them to relative times when possible.
- Colors accept `w/y/g/r/o` or `white/yellow/green/red/orange`.

## Caption Table

Captions are edited as two linked tables.

The top table is the caption block list:

```text
Start | End | Kind
0.2   | 4.0 | main
```

Select a caption row, then edit its text pieces in the lower segment table:

```text
Text       | Color
今天來看   | w
AutoCapCut | y
```

This maps to the upstream sample shape:

```python
caps=[
    (0.3, 5.0, [("video-autopilot-kit", "g"), ("demo", "y")], "main"),
    (5.3, 9.7, [("no CapCut needed", "w")], "addr"),
]
```

`Kind` controls the caption lane:

- `main`: main caption Y position.
- `addr`: address/CTA caption Y position.

Colors:

- `w`: white
- `y`: yellow
- `g`: green
- `r`: red
- `o`: orange

Caption helpers:

- `Load Template`: replaces captions with a preset sample.
- `Auto Blocks`: creates rough 4-second caption blocks across the current clip
  timeline.
- `Add Caption` / `Remove Selected`: manually manage caption blocks.
- `Add Text` / `Remove Text`: manage colored text pieces for the selected block.

## BGM Start

The GUI exposes the sample `bgm_start` option:

- `Auto highlight`: passes `"auto"` to the upstream renderer.
- `Start at 0s`: passes `0.0`.
- `Manual seconds`: passes the value from the seconds field.

## Backend CLI

The same backend can run from JSON config:

```powershell
C:\Users\User\anaconda3\Scripts\conda.exe run -n AutoCapCut python -m autocapcut_app.workflows.short_video --config path\to\job.json
```
