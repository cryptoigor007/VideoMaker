# CP11 — Final regression, manifest and full project archive

Date: 2026-10-12

## What this checkpoint contains

CP11 is a **full project snapshot**: client GUI, HTTP API/server, queue/bridge, pipeline,
media engines, scripts, docs, tests, all earlier checkpoint notes, and final validation
logs. It is not an incremental patch. Runtime Python caches and pytest cache are excluded.

## Final changes after CP10

- Unified the public speech-rate engine default as well as the main pipeline on target
  **120 WPM** (`min=120`, `max=150`). The engine no longer falls back to 135 WPM on
  code paths which call `prepare_narration()` without an explicit target.
- Updated regression and synthetic FFmpeg expectations to the 120 WPM default.
- Fixed the BGM smoke test's import setup so it runs when launched directly from the
  complete project directory/archive.
- Fixed stale live-test assumptions about empty Gemini keys, and expanded live API checks
  to test settings GET/POST, persistence, and OpenAPI schema for `remove_long_pauses`.

## Validation

- Python compilation: **PASS** (`python -m compileall -q video_maker tests`).
- Unit/regression suite: **94 passed, 2 subtests passed**.
- Live HTTP API: **79 passed, 0 failed**, including auth methods, settings API POST/GET,
  persistence, OpenAPI schema, error handling, queue, 36 concurrent requests, SSE and LAN.
- Settings-to-GUI map: **70 passed, 0 failed**.
- Tk GUI/API panel: **33 passed, 0 failed**.
- Log-thread/deadlock verification: **PASS**.
- Synthetic speech FFmpeg: **PASS**, final target 120 WPM, timestamps preserved.
- Synthetic BGM FFmpeg: **PASS**, output video/audio streams present and full audio decode.
- Final ZIP integrity: **PASS**; all entries readable, no duplicate filenames or
  runtime cache files; all entries listed in `FINAL_SOURCE_MANIFEST.json` verified.

Synthetic audio tests validate the processing pipeline, not subjective judgement of a real
narrator's tone. Pause reduction stays disabled unless the checkbox/API setting is enabled.

## Resume / recovery

All 5/5 roadmap items are complete (100%; 0 remaining). On an interruption, extract this
archive, read `checkpoints/INDEX.md` and `checkpoints/ROADMAP_STATUS.json`, then continue from
CP11. Earlier full snapshots CP07–CP10 are also supplied separately.
