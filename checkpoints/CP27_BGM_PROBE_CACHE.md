# CP27 — BGM output probe cache and pass-budget regression

## Finding
During finalization, the same staged wide/vertical/Short source can be mixed for several platforms. `mix_bgm` already caches duration probes, but `verify_bgm_output` used its own `_probe` and launched ffprobe against the unchanged source for every platform output. Each destination still needs an output probe; repeated source metadata probes are redundant.

## Change
- Added a bounded 2048-entry metadata cache to `video_maker/engines/bgm_validation.py`.
- Cache key: canonical real path, ffprobe executable selector, mtime nanoseconds, and size. A rewrite or replacement changes the key and invalidates the cached result.
- Cache applies only to metadata (`duration`, stream types and codecs). Every new mixed output is probed and validated independently; no media output is accepted based only on its cache record.
- Existing file-size checks and source/output video+audio stream checks remain fail-closed.
- This does not decode the entire MP4; `verify_bgm_output` performs ffprobe metadata checks only. The actual mix has already completed via FFmpeg with `-c:v copy` and AAC audio.

## Measured regression
- New test with two outputs from one unchanged source: 3 ffprobe invocations (one source + one per output) instead of 4 (source repeatedly + two outputs).
- Cache invalidation test: increasing the same source file's size causes a fresh ffprobe invocation.
- Full regression: 180 passed, 1 headless display-only skip, 23 subtests passed.
- GUI layout (1024×768): PASS; GUI settings 70/70; GUI/API panel 35/35; Chromium responsive smoke PASS; speech and BGM synthetic FFmpeg smoke PASS.

## Effect on processing
No FFmpeg encode or audio mix was removed from required outputs, and no extra media pass was added. The optimization only avoids repeated ffprobe process launches for the same unchanged source when finalizing several platform variants.
