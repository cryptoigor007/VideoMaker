# CP24 package verification

- Source archive: `VideoMaker_FULL_R57_CP24_RENDER_PASS_MAP.zip`.
- Project complete tree under `VideoMaker/`; excludes local `.env`, runtime `_tmp`, `.pytest_cache`, `__pycache__`, and bytecode.
- ZIP CRC result: `PASS`.
- Archive entries: 191; duplicate paths: 0.
- Source manifest: 189 files; SHA-256/size mismatches: 0.
- Extracted `compileall`: PASS.
- Extracted regression: `166 passed, 1 skipped, 23 subtests passed in 1.19s` (exit=0).
- Base test result: `166 passed, 1 skipped, 23 subtests passed`; skip is a display-only GUI check without a display server.
- CP24 is a map-only checkpoint; no production pipeline/engine source was changed.
