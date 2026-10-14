# CP16 — Final full-source package and regression

Date: 2026-10-12

## Purpose

Согласованный полный снимок исходного кода VideoMaker r57-api.3: GUI-клиент, HTTP API/сервер, очередь, pipeline, engines, настройки, тесты, документация и история чекпоинтов. Метаданные статуса дорожной карты приведены к фактическому CP16. Этот ZIP является полным исходным проектом, а не patch-only архивом.

## Fresh verification from the extracted source tree

- `python -m pytest -q --disable-warnings --maxfail=1`: **118 passed, 2 subtests passed**.
- `python -m compileall -q video_maker tests`: PASS.
- `python tests/synthetic_ffmpeg_smoke.py`: PASS — synthetic audio, final WPM 120.0, word-level timestamps preserved.
- `python tests/synthetic_bgm_mix_smoke.py`: PASS — H.264/AAC output, full audio decode passed.
- `bash -n start.command start-headless.command`: PASS.
- `python -m video_maker.main --help`: PASS; help output includes `--headless`, `--api-port`, and `--no-api`.
- No API tokens or private-key patterns found in source tree during release scan.
- Archive and manifest validation are performed after ZIP creation; SHA-256 is published alongside the artifact.

## Included

- Full `video_maker/` source tree: GUI, API/server/web UI, pipeline, engines, config, external helpers and app icon.
- `tests/`, documentation, `.env.example`, `requirements.txt`, launch scripts, audit notes, checkpoint reports and source manifests.
- Checkpoint logs and history sufficient to understand the changes from CP07 to CP16.

## Intentionally omitted

Local `.env` credentials, Python virtual environments, runtime caches, bytecode, pytest caches, temporary renders and generated logs. These are environment-specific and should not be shipped as source.

## Known limitations — not hidden by passing tests

- FFmpeg/ffprobe calls in some auxiliary paths still lack a unified per-operation timeout policy; a subprocess can potentially hang. Avoid setting a single short timeout for all 4K rendering operations.
- `target_lufs` applies in the BGM mix path; voice-only output does not get a separate loudnorm pass. This should be made explicit as a UI choice or label in future work.
- Native Apple Silicon/macOS MLX transcription and a real 8–10 minute user video were not exercised in this Linux test environment. Synthetic signal tests cannot validate human voice timbre or perceived pacing.
- No successful real upload to YouTube/TikTok or other platforms is claimed.
- Media deduplication targets identical content/source + BGM/render recipe, not semantic similarity between different source intervals.

## Recovery

1. Extract the full ZIP into a new directory.
2. Read `README.md`, `AUDIT_DEEP_REVIEW.md` and `checkpoints/INDEX.md`.
3. Verify file hashes using `checkpoints/CP16_SOURCE_MANIFEST.json` and `checkpoints/FINAL_SOURCE_MANIFEST.json` (same final file inventory; manifest files omit themselves to avoid recursion).
4. Run compileall and pytest commands above.
5. On target Mac, install according to README and first run a short sample with pause reduction disabled; compare with the same sample with it enabled before using it for a full episode.
