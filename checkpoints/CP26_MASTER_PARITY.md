# CP26 — H/V shared narration parity and redundant-pass guard

## Changes
- Added integration-level unit coverage proving that `MasterBuilder` passes exactly the same `narration_enhanced_*.wav` to the horizontal and native-vertical master builders.
- Added tests proving `FinalHorizontal` and `FinalVertical` do not call the legacy post-render voice enhancement when `voice_enhance_applied=True`.
- Removed an obsolete trigger in `FinalHorizontal` that invoked the legacy audio-post helper solely because `add_bgm`/`bgm_folder` were set, even though BGM mixing is deferred to `FinalizeStage`. That condition caused unnecessary Python/helper activity (not an FFmpeg encode); the music mixing stage itself remains unchanged.
- Kept the compatibility fallback for direct branch invocation when voice enhancement is enabled but the shared voice track has not been prepared.

## Pass-count guarantees
- Production path: one shared voice-enhancement pass before master construction.
- Existing H and V master encode counts unchanged; H/V both consume the same prepared narration WAV.
- No additional video encode or transcription/Gemini pass introduced.
- Standalone synthetic 8-second benchmark: legacy H+V post-render enhancement uses 6 FFmpeg + 4 ffprobe calls; common lossless path uses 1 FFmpeg + 1 ffprobe call; measured duration drift 0.000 s. The reduction is limited to the voice-enhancement subtask, not the full video runtime.

## Verification
- New targeted tests: 12 passed across CP25 + CP26 voice-sharing tests.
- Full regression: 178 passed, 1 skipped (headless display-dependent test), 23 subtests passed.
- compileall: PASS.
- Synthetic speech: PASS, final 120 WPM, word-level timestamps preserved.
- Synthetic BGM: PASS, H.264/AAC, full audio decode PASS.
- Browser responsive smoke: PASS.
