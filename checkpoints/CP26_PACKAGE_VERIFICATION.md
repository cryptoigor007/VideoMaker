# CP26 package verification — final

- Complete source/recovery archive (GUI, API/server, pipeline/engines, tests, docs and history through CP26); not patch-only.
- ZIP entries: **204**; duplicate paths: **0**; ZIP CRC: **PASS**.
- CP26 source manifest lists **202 files**; after extraction, size/SHA-256 mismatches: **0**.
- Local `.env`: **absent**; `.env.example` is included. Runtime `_tmp`, generated renders, pytest caches and Python bytecode are excluded.
- Manifest and this verification note are excluded from the manifest to prevent recursive hashing, but both are included in the ZIP.
- Extracted compileall: **PASS**.
- Fresh pytest on extracted project: **178 passed, 1 skipped, 23 subtests passed**. The single skip is the display-only test when run without X server.
- CP26 targeted H/V voice-sharing tests: **12 passed**.
- Synthetic speech FFmpeg smoke: **PASS**, final 120 WPM and word-level timestamps preserved.
- Synthetic BGM smoke: **PASS**, H.264/AAC and complete audio decode.
- Responsive Chromium web smoke: **PASS** on 1365×900 desktop and 390×844 mobile.
- Voice enhancement synthetic micro-benchmark: **6→1 FFmpeg calls**, **4→1 ffprobe calls**, **0.000 s duration drift** on an 8-second test sample. Not a whole-episode runtime claim.
