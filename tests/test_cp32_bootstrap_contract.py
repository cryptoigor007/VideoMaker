from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_scripts_have_valid_bash_syntax() -> None:
    for name in ("start.command", "start-headless.command", "setup.command", "doctor.command"):
        result = subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_launchers_have_shell_shebangs() -> None:
    for name in ("start.command", "start-headless.command", "setup.command", "doctor.command"):
        assert (ROOT / name).read_text(encoding="utf-8").startswith("#!/bin/bash\n")


def test_runtime_requirements_are_separate_from_test_tools() -> None:
    runtime = (ROOT / "requirements-runtime.txt").read_text(encoding="utf-8").lower()
    dev = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "google-genai" in runtime
    assert "imageio-ffmpeg" in runtime
    assert "mlx-whisper" not in runtime  # Installed only on supported Apple Silicon macOS.
    assert "pytest" not in runtime
    assert "pytest" in dev


def test_startup_bootstraps_when_environment_is_missing() -> None:
    launcher = (ROOT / "start.command").read_text(encoding="utf-8")
    assert "setup.command" in launcher
    assert "--check-only" in launcher
    assert "requirements-runtime.txt" in (ROOT / "setup.command").read_text(encoding="utf-8")
    assert "video_maker.main" in launcher
    assert "PIPESTATUS[0]" in launcher


def test_setup_fails_early_on_unsupported_platform_and_checks_runtime() -> None:
    setup = (ROOT / "setup.command").read_text(encoding="utf-8")
    assert '"$(uname -s)" != "Darwin"' in setup
    assert '"$ARCH" != "arm64"' in setup
    assert "sw_vers -productVersion" in setup
    assert "mlx_whisper" in setup
    assert "imageio_ffmpeg.get_ffmpeg_exe()" in setup
    assert "system_media_tools_ok" in setup
    assert "brew install ffmpeg" in setup
    assert "--check-only" in setup


def test_doctor_never_prints_env_contents() -> None:
    doctor = (ROOT / "doctor.command").read_text(encoding="utf-8")
    assert "read-only diagnostics" in doctor
    assert "contents intentionally not displayed" in doctor
    assert "ffprobe" in doctor
    assert "cat .env" not in doctor
