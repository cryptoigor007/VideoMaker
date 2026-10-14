# CP25 — shared lossless voice enhancement

## Objective
Remove duplicate processing of the same narration for the horizontal and vertical masters without adding another full video encode.

## Implementation
- `TranscribeStage` now applies optional voice enhancement to the prepared narration once, before `MasterBuilder`.
- The shared result is a 48 kHz PCM S16LE WAV, avoiding an intermediate lossy AAC pass. Both horizontal and native-vertical master branches use the same narration file.
- Duration is probed after enhancement; a drift over `max(0.15s, 0.5% of source duration)` fails early rather than silently desynchronizing captions.
- `PipelineContext.voice_enhance_applied` marks the shared result. Final branches skip redundant enhancement when this flag is true. The direct-branch fallback remains for integrations which invoke a final branch without the regular transcription stage.
- Checkpoint metadata stores both the requested and applied state. If the voice-enhancement setting changes on resume after transcription, the transcription stage and dependent stages are invalidated, preventing stale enhanced audio from being reused.
- Voice enhancement emits PCM for `.wav` outputs while retaining the prior AAC behavior for `.aac`/other output formats.

## Measured change
The reproducible 8-second synthetic benchmark recorded 6 FFmpeg calls + 4 ffprobe calls for the two legacy branch paths versus 1 FFmpeg + 1 ffprobe call for the shared path. Five FFmpeg calls were removed (83.3% fewer FFmpeg invocations for this subtask); duration drift was 0.000 seconds. Wall times are in `CP25_VOICE_ENHANCE_SYNTHETIC_BENCHMARK.json`.

This is a local synthetic micro-benchmark of voice enhancement only, not a full-video runtime claim. Main horizontal/vertical video encode count has not increased; the optimisation replaces two post-render audio enhancement chains with one shared audio-only filter before master construction.

## Verification
- `pytest`: 174 passed, 1 skipped, 23 subtests passed.
- Python compileall: PASS.
- GUI layout at 1024×768: PASS.
- GUI settings mapping: 70/70.
- GUI/API panel: 35/35.
- Live loopback API: 72/72.
- Chromium responsive panel: PASS at desktop 1365×900 and mobile 390×844.
- Synthetic speech and BGM FFmpeg smoke: PASS.

## Files changed
- `video_maker/pipeline/stages.py`
- `video_maker/pipeline/branches.py`
- `video_maker/pipeline/checkpoint.py`
- `video_maker/engines/audio.py`
- `tests/test_voice_enhance_pass_cp25.py`
- `tests/synthetic_voice_enhance_smoke_cp25.py`
- user-facing docs/version/checkpoint records.
