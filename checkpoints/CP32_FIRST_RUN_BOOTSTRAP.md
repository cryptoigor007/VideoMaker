# CP32 — first-run bootstrap and launchability

Date: 2026-10-12
Base: CP31 readiness/type fixes

## Changes

- `start.command` now runs an environment check and automatically calls `setup.command` if a venv is missing, dependencies differ, imports fail, or bundled FFmpeg does not launch.
- `setup.command` supports `--check-only`, creates a project-local `.venv`, installs runtime dependencies, installs MLX Whisper only on a supported platform, validates real imports, and runs bundled FFmpeg `-version` with a 10-second timeout.
- Installer requires native ARM64 macOS 14+ because the current transcription engine is MLX Whisper. It tries native Python 3.11–3.14 candidates, preferring 3.13/3.12, and preserves the previous venv if fresh installation cannot complete.
- Added `requirements-runtime.txt` so a normal installation does not install pytest or test-only tools.
- Added read-only `doctor.command` diagnostics.
- Marked launchers executable in the ZIP.
- Updated README with exact first-run behavior and clearly separated program setup from user media/API-key configuration.

## Validation

- `bash -n` on `start.command`, `start-headless.command`, `setup.command`, `doctor.command`: PASS.
- `setup.command --help`: PASS.
- `compileall`: PASS.
- Full pytest: 206 passed, 1 skipped (GUI-only test without a display), 23 subtests passed.
- CP32 installer contract tests cover script syntax, executable bits, runtime/dev dependency separation, automatic bootstrap, platform guards and no secret printing.

## Environment-dependent limitation

This validation container is Linux and does not have native macOS/Apple Silicon MLX runtime. The installer correctly refuses to install/run MLX Whisper on unsupported hosts. A full end-to-end package installation and GUI start must still be confirmed on the target Apple Silicon Mac. The initial Whisper model weights are downloaded on first transcription; they are intentionally not embedded in the ZIP.

## Resume

CP32 complete. Next: CP33 checkpoint persistence and source/settings fingerprints; CP34 API bridge cancellation and graceful GUI shutdown; CP35 transcription model/settings consistency; CP36 output disk-space guard, dead code/dependency cleanup and final full archive validation.
