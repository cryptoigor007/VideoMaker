# CP33 — checkpoint integrity and first-run dependency readiness

## Scope

This full checkpoint carries the CP32 first-run bootstrap and fixes persistence/resume invalidation. It also corrects the bootstrap contract for actual use of `ffmpeg`/`ffprobe` through `PATH` in audio/checkpoint modules: setup verifies both executable tools and installs/reinstalls the Homebrew formula if needed. Homebrew must exist before bootstrap; unsupported OS/architecture is rejected with a clear message.

## Checkpoint integrity

- Unique, atomic transcription/analysis sidecar files; main checkpoint is committed only after its referenced artifacts are safely written.
- A sidecar or main checkpoint write error raises `CheckpointPersistenceError`, preserves the prior main checkpoint and its referenced files, and does not mutate the committed-stage list.
- Resume fingerprints cover source audio content, transcription controls, analysis options, render source inventory/output choices, and BGM/finalization choices. A changed fingerprint invalidates the earliest affected stage and downstream data.
- Source audio fingerprint uses SHA-256; media-folder inventory is cached for the current pipeline context and includes size/mtime/symlink and edge samples to catch common same-stat replacements without fully hashing every large video.

## Verification

- `python -m compileall -q video_maker tests`: PASS
- Standard headless suite: `214 passed, 1 skipped, 23 subtests passed`; the skipped test requires a display.
- Xvfb 1024×768: `215 passed, 23 subtests passed`.
- `bash -n start.command setup.command doctor.command start-headless.command`: PASS
- `setup.command --help`: PASS
- `start.command --headless` in the Linux validation container exits nonzero with a clear unsupported-platform message. This is expected and is not a native macOS run.
- The final ZIP is re-extracted to a separate directory; hashes, ZIP CRC, executable launcher permissions, absence of duplicate ZIP paths and `.env` exclusion are checked. Tests are run from the extracted tree.

## Known limitation

A real installation and MLX transcription run on the user's Apple Silicon Mac still needs to be verified on that Mac; the current validation runtime is Linux and must not be described as native macOS validation.

## Resume

Next checkpoint CP34: ensure timed-out TkBridge calls that have not started are cancelled, then make GUI shutdown graceful without silently dropping pending checkpoint writes. CP35: make transcription controls agree with the actual MLX engine. CP36: writable-output/disk-space preflight, per-output run lock and safe dead-code/dependency cleanup.
