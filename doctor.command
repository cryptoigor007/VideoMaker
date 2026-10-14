#!/bin/bash
# VideoMaker diagnostic check; does not change configuration or install software.
set -u
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
echo "VideoMaker Doctor — read-only diagnostics"
echo "Project: $(pwd)"
echo "OS: $(uname -s) $(uname -m)"
if [ "$(uname -s)" = "Darwin" ]; then sw_vers -productVersion; else echo "NOTE: MLX transcription needs Apple Silicon macOS."; fi
if command -v python3 >/dev/null 2>&1; then python3 --version; else echo "FAIL: python3 not found"; fi
for tool in ffmpeg ffprobe; do
  if command -v "$tool" >/dev/null 2>&1 && "$tool" -version >/dev/null 2>&1; then
    echo "OK  $tool: $(command -v "$tool")"
  else
    echo "MISS $tool — start.command installs via existing Homebrew when available."
  fi
done
if [ -x .venv/bin/python ]; then
  .venv/bin/python --version
  .venv/bin/python - <<'PY'
import importlib.util
mods=["google.genai","dotenv","PIL","numpy","psutil","imageio_ffmpeg","mlx_whisper","tkinter"]
for name in mods:
    try: ok=importlib.util.find_spec(name) is not None
    except Exception: ok=False
    print(("OK  " if ok else "MISS") + " " + name)
try:
    import imageio_ffmpeg, os
    ff=imageio_ffmpeg.get_ffmpeg_exe()
    print(("OK  " if os.path.isfile(ff) and os.access(ff,os.X_OK) else "MISS") + " bundled FFmpeg: " + str(ff))
except Exception as exc: print("MISS bundled FFmpeg: " + str(exc))
PY
else
  echo "MISS .venv — start.command installs the runtime environment automatically."
fi
if [ -f .env ]; then echo "INFO .env exists (contents intentionally not displayed)."; else echo "INFO no .env; Gemini key can be configured in the application."; fi
if [ -x start.command ]; then echo "OK  launcher executable"; else echo "WARN start.command lacks executable bit; use chmod +x start.command setup.command doctor.command"; fi
