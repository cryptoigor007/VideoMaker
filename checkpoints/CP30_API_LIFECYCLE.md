# CP30 — API lifecycle and truthful run status

Date: 2026-10-12
Base: CP29 deep-audit snapshot

## Fixed

1. `VideoMakerAPI.start()` refuses to bind when `enabled is not True`; attempting to start a disabled API closes any existing listener.
2. `VideoMakerAPI.stop()` clears references, closes the listener and joins the server thread safely. Repeated `start()` is idempotent while already listening.
3. `/api/config` updates are transactional. Disabling the service closes the actual listening socket. If rebinding to a new port/address fails, the previous configuration is restored and the previous listener is restarted where possible.
4. `/api/run` no longer returns `started=true` merely because `App._start()` returned without raising. It checks the app's synchronous `running` state after GUI-side validation; rejected starts return HTTP 409 with `started=false`.
5. Queue launch now marks an item failed if the GUI refused to start the pipeline after its own additional preflight checks.
6. Malformed API config files whose JSON root is not an object (or cannot be read) fail closed with `enabled=false` rather than throwing `TypeError` or silently enabling the server.

## Regression tests

`tests/test_cp30_api_lifecycle.py` adds seven tests for disabled startup, listener closure, transactional rebind rollback, truthful run results, malformed JSON root, and idempotent start.

## Verification

- Targeted CP30/API security suite: 25 passed, 2 subtests passed.
- Full suite: 189 passed, 1 skipped (display-only GUI test without a display), 23 subtests passed.
- Python compileall: PASS.
- Complete ZIP extracted and checked independently; CRC/manifest SHA-256 verified.

## Remaining high-priority items

- CP29-02: results readiness must require an output video rather than metadata-only.
- CP29-04/05: checkpoint artifact-write failures and input/settings fingerprint invalidation.
- CP29-06: timed-out TkBridge calls must not execute later.
- CP29-08/10: strict boolean setting conversion and malformed queue JSON roots.
- CP29-11: Whisper settings must affect execution or be removed/marked unsupported.
