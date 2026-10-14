# CP28 package verification — final

- Complete source/recovery archive: GUI client, API/server, pipeline/engines, all tests, configuration example, docs, audit logs, UI screenshots and checkpoints CP11–CP28.
- ZIP entries: **213**; duplicate paths: **0**; ZIP CRC: **PASS**.
- Source manifest lists **211 files**; after extraction, size/SHA-256 mismatches: **0**.
- Local `.env`: **absent**; `.env.example` is included. Runtime `_tmp`, generated renders, pytest caches and Python bytecode are excluded.
- The source manifest and this verification note are excluded from the manifest to avoid recursive hashing but are both included in the ZIP.
- Extracted `compileall`: **PASS**.
- Fresh pytest from extracted project: **183 passed, 1 skipped, 23 subtests passed**. The skip is the display-dependent Tk layout test without a display; the GUI layout test separately passes under Xvfb at 1024×768.
- GUI settings map: **70/70**; GUI/API panel: **35/35**; Chromium responsive test: **PASS** at desktop 1365×900 and mobile 390×844.
- Synthetic speech FFmpeg smoke: **PASS**, 120 WPM, word timing preserved.
- Synthetic BGM FFmpeg smoke: **PASS**, H.264/AAC and complete audio decode.
- CP27 BGM metadata cache tests confirm unchanged source files are probed once across repeated outputs and changed size invalidates the cache.
- Final static scan: 32 `subprocess.run/call/check_call/check_output` calls; **0** lacking explicit timeout; **0** `shell=True`; **0** disabled legacy BGM branches; no high-confidence API-secret patterns matched.
- Live API integration previously passed **72/72** in an isolated loopback setup; API/server sources were unchanged in CP26–CP28.
- Voice-enhancement benchmark is a synthetic 8-second subtask micro-benchmark only (6→1 FFmpeg calls, 4→1 ffprobe calls, 0.000 s drift), not a full-episode runtime claim.
