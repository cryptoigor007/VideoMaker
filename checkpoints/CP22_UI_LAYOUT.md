# CP22 — desktop UI layout and visibility

## Base
CP21 working tree derived from the full CP20 project snapshot.

## Changes
- Adaptive, centered main-window dimensions bounded by display resolution.
- Main and Intro/Middle/Outro option pages are scrollable; primary action/progress remains fixed on the main tab.
- Horizontal and vertical B-roll selectors use dedicated rows, with dynamic single-column layout below approximately 1020 px.
- Log and HTTP panel moved to dedicated `Журнал и пульт` tab; API toggles/buttons stay inside the visible viewport on small windows.
- Settings modal adapts to screen dimensions and uses grid row 1 for fixed Save action; prompt text has a scrollbar; keys/prompt/extra text boxes use consistent dark colours.
- Improved contrast and active/disabled/focus styles.

## Verification
- `xvfb-run -a pytest -q tests/test_gui_layout_cp22.py`: 1 passed.
- Full headless `pytest -q`: 161 passed, 1 skipped, 23 subtests passed.
- Full suite with `xvfb-run -a -s '-screen 0 1024x768x24' pytest -q`: 162 passed, 23 subtests passed.
- `tests/verify_api_settings.py`: 70 successful, 0 failed.
- `tests/verify_app_gui.py`: 35 successful, 0 failed, including loopback HTTP checks.
- Synthetic speech test: 120 WPM and word timestamps preserved; synthetic BGM audio fully decodes.
- `xvfb-run -a -s '-screen 0 1024x768x24' pytest -q tests/test_gui_layout_cp22.py`: 1 passed.
- `xvfb-run -a python tests/verify_app_gui.py`: 35 checks passed, 0 failed.
- Screenshots stored in `checkpoints/CP22_MAIN_1024x768.png`, `CP22_MONITOR_1024x768.png`, `CP22_SETTINGS_1024x768.png`, and `CP22_SETTINGS_DETAILS_1024x768.png`.

## Processing speed
No pipeline/engine source changed in CP22, so this checkpoint does not add a video encode, normalization, transcription or analysis pass.
