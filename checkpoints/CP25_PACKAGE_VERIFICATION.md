# CP25 package verification — final

- Full-source recovery archive, including GUI client, API/server, media pipeline/engines, tests, documentation, historical checkpoints and CP25 benchmark.
- Local `.env`, runtime `_tmp`, render outputs, pytest caches, Python bytecode and `__pycache__` are excluded; `.env.example` is included.
- ZIP entries: **198**, duplicate paths: **0**, ZIP CRC: **PASS**.
- CP25 source manifest lists **196 files**; post-extraction size/SHA-256 mismatches: **0**. The manifest and this verification note are excluded from the manifest to prevent recursive hashes but included in the ZIP.
- Extracted compileall: **PASS**.
- Fresh pytest from extracted project: **174 passed, 1 skipped, 23 subtests passed** (1.21s); the skip is the display-dependent GUI test in headless mode.
- Synthetic voice and BGM FFmpeg smoke: **PASS**; 120 WPM with word timestamps preserved; BGM output fully decodes.
- GUI layout under Xvfb 1024×768: **PASS**; GUI settings 70/70; GUI/API panel 35/35; browser responsive smoke: **PASS** at 1365×900 and 390×844; live loopback API regression: **72/72** in an isolated temporary HOME.
- Voice-enhancement performance claim is intentionally scoped to the 8-second synthetic audio micro-benchmark: 6→1 FFmpeg calls, 4→1 ffprobe calls, and 0.000s duration drift. This is **not** a whole-episode timing claim.
