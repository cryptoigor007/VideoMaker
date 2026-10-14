# CP28 — final deep audit and recovery status

## Changes in CP28
- Removed four unreachable legacy BGM blocks guarded by `if False` from `pipeline/branches.py` and `pipeline/shorts.py`. They were already impossible to execute; actual platform-specific BGM remains in `FinalizeStage`. This removes misleading dead code without changing the media path.
- Added `tests/test_subprocess_timeout_policy_cp28.py` to prevent regressions: direct `subprocess.run/call/check_call/check_output` calls must specify a timeout; disabled legacy BGM branches must not return; BGM metadata cache remains bounded and keyed by file modification time and size.
- Preserved the existing power-management Popen workers; these intentionally launch long-lived `caffeinate`/`systemd-inhibit`/shutdown processes and are managed by lifecycle logic.

## Final source audit
- Production source: **32** `subprocess.run/call/check_call/check_output` calls; **0** are missing explicit timeouts.
- **0** `shell=True` calls.
- **0** unreachable `if False ... BGM` legacy branches remain.
- No files matched common high-confidence API-secret patterns (Google API key, GitHub PAT, `sk-...`, long bearer token). Local `.env` is excluded; `.env.example` is included.
- UI checkpoints CP22 and CP23 remain in the source archive, including responsive-layout tests and screenshots (desktop 1365×900, mobile 390×844, desktop GUI 1024×768).

## Media pass / throughput conclusions
- Whisper transcription is performed once per source per completed checkpoint; Gemini analysis is one batched stage (network/model retries may still happen on errors).
- Optional voice enhancement is now one shared lossless WAV pass before master construction. H/V masters use that path; final branches skip repeated extraction/enhancement/remux when the shared pass is recorded.
- Horizontal master encode remains one; native vertical master is built only when native vertical B-roll exists. When using vertical background, geometry and enabled ASS captions remain combined in one final encode.
- Shorts use stream-copy trim first and only invoke fallback re-encode when the cut is too short/invalid; captions/hooks are burned in one final encode per unique Short where needed.
- BGM is finalized per publication asset and uses `-c:v copy` with AAC audio. Validation probes stream metadata; it does not perform an additional full video decode. The CP27 metadata cache avoids probing the same unchanged source again for every platform, while each new output is checked independently.
- Deduplication prevents identical Shorts with byte-identical source + chosen music + matching settings from repeating the same mix/render. Different selected tracks remain independent variants.
- No additional transcription, Gemini, or full-video encode stage was added in CP25–CP28. CP27 only reduced repeated ffprobe launches; CP28 removed unreachable code and added static tests.

## Final validation
- Full regression after CP28 code changes: **183 passed, 1 skipped, 23 subtests passed**. The headless skip is a display-only Tk layout check; that check passes under Xvfb at 1024×768.
- `compileall`: PASS.
- GUI layout Xvfb 1024×768: PASS.
- GUI settings mapping: 70/70.
- GUI/API control panel: 35/35.
- Responsive Chromium: PASS at 1365×900 and 390×844 with no horizontal overflow.
- Speech FFmpeg smoke: PASS, 120 WPM, word-level subtitle timestamps preserved.
- BGM FFmpeg smoke: PASS, H.264/AAC and full audio decode PASS.
- Voice-enhancement synthetic microbenchmark: 6→1 FFmpeg calls and 4→1 ffprobe calls for the voice-enhancement subtask, no duration drift. This is not a full episode benchmark.
- Live API integration: previously confirmed 72/72 in isolated test environment; the API/server source was not changed in CP26–CP28.

## Explicitly unverified
- Long, real narration transcription through MLX Whisper on the user's Apple Silicon Mac.
- Full episode wall time/CPU/SSD thermals on the actual Mac; the benchmark is limited to a synthetic 8-second voice-enhancement sample.
- Subjective human-voice timbre and perceived BGM loudness; automated codec/timing checks cannot replace listening.
- HTTP is not TLS-encrypted and has no request-rate limiting. Do not expose it directly to the public internet; use a VPN for remote access.
