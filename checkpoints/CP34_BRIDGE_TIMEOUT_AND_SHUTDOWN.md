# CP34 — TkBridge timeout cancellation and graceful GUI shutdown

## Changes

- Each TkBridge request has pending/running/cancelled/done states. Callers timing out while requests are pending cancel them; the Tk pump skips cancelled callbacks, preventing a timed-out queued run request from launching a render later.
- `stop()` rejects new requests and wakes queued synchronous callers; the start transition is serialized with stop so a dequeued but not yet started callback cannot slip through after shutdown.
- App close while a pipeline is active requests cancellation, kills the current FFmpeg child processes, and keeps Tk alive while waiting for the pipeline worker and its `finally` block to finish. A wait warning after 120 seconds offers to keep waiting; declining leaves the UI open rather than discarding a running worker.
- Removed forced `os._exit(0)` from normal GUI close. Saves settings, stops sleep prevention, queue/listener/bridge, and destroys Tk only after the pipeline has completed, unless the user exits the process externally.

## Verification

- Targeted bridge/shutdown tests: 5 passed.
- `python -m compileall -q video_maker tests`: PASS.
- Full headless suite: 219 passed, 1 display-only skip, 23 subtests passed.
- Full suite under Xvfb 1024×768: 220 passed, 23 subtests passed.
- No native macOS run performed in the current Linux environment.

## Resume

Next CP35: resolve misleading Whisper UI controls against the MLX engine's supported model/device options. Then CP36: output disk/writability preflight and per-output interprocess lock. Continue with dead-code/dependency audit and the remaining CP29 open findings.
