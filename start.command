#!/bin/bash
# VideoMaker: double-click launcher with first-run dependency bootstrap.
set -u
cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HEADLESS=0
for arg in "$@"; do [ "$arg" = "--headless" ] && HEADLESS=1; done

finish() {
  local code="$1"
  echo
  echo "VideoMaker завершил работу с кодом: $code"
  if [ "$HEADLESS" -eq 0 ]; then
    echo "Нажмите Enter, чтобы закрыть это окно..."
    read -r _ || true
  fi
  exit "$code"
}

echo "========================================"
echo "       VideoMaker starting..."
echo "========================================"
echo "Папка: $PROJECT_DIR"
echo

# Self-prepare environment on first start (or repair an incomplete environment).
# The read-only check validates imports AND actually starts bundled FFmpeg.
if ! "$PROJECT_DIR/setup.command" --check-only >/dev/null 2>&1; then
  echo "Первый запуск или окружение повреждено/устарело — подготовка окружения."
  "$PROJECT_DIR/setup.command" || finish 1
fi

PYTHON="$PROJECT_DIR/.venv/bin/python"
if ! "$PYTHON" -c 'import tkinter, mlx_whisper, google.genai, imageio_ffmpeg' >/dev/null 2>&1; then
  echo "Ошибка: не все обязательные модули импортируются. Запустите ./setup.command для восстановления среды."
  finish 1
fi
mkdir -p "$HOME/video_maker" || finish 1
echo "Python: $($PYTHON --version 2>&1)"
echo "Лог: $PROJECT_DIR/videomaker.log"
echo
set +e
"$PYTHON" -m video_maker.main "$@" 2>&1 | tee -a "$PROJECT_DIR/videomaker.log"
EXIT_CODE=${PIPESTATUS[0]}
set -e
finish "$EXIT_CODE"
