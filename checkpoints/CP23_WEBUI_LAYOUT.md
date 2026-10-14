# CP23 — responsive browser control panel

## Improvements
- Desktop at >=920px: status and queue appear side-by-side; log stays full-width below.
- Mobile <=480px: one-column layout; no horizontal overflow at a 390px viewport.
- Higher contrast for muted text, ghost/danger/disabled buttons and borders.
- `:focus-visible` outlines, reduced-motion handling and consistent >=44px touch targets.
- Accessible names/roles for queue inputs, progress bar and log; retry/remove buttons are labelled with the associated series.
- `render(embed=True)` hides the visible title but leaves `#state` in the DOM for existing JavaScript.
- Existing functions preserved: start/cancel, add/pause/continue/clear/retry/remove queue items, log/status refresh.

## Verification
- `pytest -q tests/test_webui_accessibility_cp23.py`: 2 passed.
- `pytest -q tests/test_api_security_cp19.py tests/test_webui_accessibility_cp23.py`: 20 passed.
- Full `xvfb-run -a -s '-screen 0 1024x768x24' pytest -q`: 164 passed, 23 subtests passed.
- `python tests/verify_webui_visual_cp23.py`: PASS in Chromium at 1365×900 and 390×844; document width equals viewport width in both cases; mobile actions remain inside viewport; queue icon actions measure 44×44px.
- Screenshots: `CP23_WEBUI_DESKTOP_1365x900.png`, `CP23_WEBUI_MOBILE_390x844.png`.

## Rendering performance
Only `video_maker/api/webui.py`, tests and documentation changed. The `video_maker/pipeline/` and `video_maker/engines/` trees are unchanged relative to CP22. No video/audio encode, transcription, analysis or normalization pass was added.
