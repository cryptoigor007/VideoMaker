# CP10 — GUI/API setting and conservative speech-rate target

Date: 2026-10-12

## Complete files included

This checkpoint archive contains the **entire project tree**, including API, client GUI,
server/control code, engines, pipeline, scripts, docs, tests, and checkpoints CP07–CP10.
It is a resume-ready full snapshot, not a patch archive.

## Changes

- Added `Settings.remove_long_pauses: bool = False` and wired the value through the
  GUI check box, settings persistence (`.video_maker_settings.json`), API settings map,
  and OpenAPI schema.
- Added a readable Russian label explaining that long pauses are optional to trim;
  setting stays off by default.
- Pipeline now passes `min_wpm=120`, `target_wpm=120`, `max_wpm=150` to narration
  preparation. The voice already inside the allowed range is not sped up just to hit
  the midpoint.
- Added regression tests for API-to-GUI setting propagation and exact WPM/checkbox
  arguments reaching `prepare_narration`.
- Fixed a stale live-verifier assertion: an empty secret is correctly returned as an
  empty string; the test now injects a synthetic non-empty secret to verify masking.
- Updated API, version, and change-log documentation to r57-api.2.

## Validation after changes

- Python compilation: **PASS** (`python -m compileall -q video_maker tests`).
- Unit/regression suite: **94 passed, 2 subtests passed**.
- Live settings-to-GUI map: **70 checks passed, 0 failed**.
- Live GUI/API control panel on Tk mainloop: **33 checks passed, 0 failed**.
- Synthetic FFmpeg pause-compression integration test: **PASS**.

## Resume position

Completed roadmap tasks 4/5 (80%). Remaining: CP11 final regression sweep, inspect archive
contents, create a final resume-ready deliverable and summarize exact checks / hashes.
