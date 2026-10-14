# CP31 — readiness, strict types, and preflight validation

Date: 2026-10-12
Base: CP30 API lifecycle fixes

## Fixed

1. Result discovery reports `ready=true` only when at least one valid output video exists; metadata-only directories do not count as finished releases.
2. If a vertical result or Shorts branch is enabled, an empty selected vertical B-roll folder is rejected during settings validation, before transcription/analysis/rendering.
3. API values that represent booleans require actual JSON booleans; strings such as `"false"` no longer become truthy settings.
4. Damaged queue state with a non-object JSON root is handled as invalid state and recovered safely instead of raising at initialization.
5. Queue pause/duplicate flags and related typed options use strict validation and fail safely on invalid types.

## Verification

- Targeted CP31 suite: PASS (`tests/test_cp31_readiness_and_types.py`).
- Full pytest: 200 passed, 1 skipped (display-only layout test without a display), 23 subtests passed.
- Python compileall: PASS.
- Existing API lifecycle tests from CP30 remain green.

## Resume

Resume at CP32 for first-run bootstrap and installability, then CP33 for checkpoint persistence/resume invalidation, CP34 for TkBridge timeouts and shutdown, CP35 for Whisper configuration parity, and CP36 for output/storage robustness and dead-code/dependency cleanup.
