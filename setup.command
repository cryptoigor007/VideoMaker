#!/bin/bash
# VideoMaker first-run installer: creates a local .venv and installs runtime dependencies.
set -u
cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

say_error() { printf '\n[ОШИБКА] %s\n' "$1" >&2; }
say_info() { printf '[VideoMaker setup] %s\n' "$1"; }

# `--check-only` never installs packages or changes files.
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=1 ;;
    --help|-h)
      echo "Использование: ./setup.command [--check-only]"
      echo "По умолчанию проверяет macOS/Apple Silicon, создаёт .venv и устанавливает зависимости."
      exit 0 ;;
    *) say_error "Неизвестный параметр: $arg"; exit 2 ;;
  esac
done

runtime_import_check() {
  local py="$1"
  "$py" - <<'PY'
import importlib, os, subprocess, sys
required = ["google.genai", "dotenv", "PIL", "numpy", "psutil", "imageio_ffmpeg", "mlx_whisper", "tkinter"]
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        print(f"Runtime import failed: {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
import imageio_ffmpeg
ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
if not os.path.isfile(ffmpeg) or not os.access(ffmpeg, os.X_OK):
    print("Bundled FFmpeg missing/not executable: " + str(ffmpeg), file=sys.stderr)
    raise SystemExit(1)
try:
    probe=subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10, check=False)
except Exception as exc:
    print(f"Bundled FFmpeg could not be started: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(1)
if probe.returncode != 0:
    print("Bundled FFmpeg -version failed: " + (probe.stderr or "")[-500:], file=sys.stderr)
    raise SystemExit(1)
print("Runtime imports and bundled FFmpeg execution: OK")
PY
}
requirements_hash() { shasum -a 256 "$PROJECT_DIR/requirements-runtime.txt" | awk '{print $1}'; }
system_media_tools_ok() {
  command -v ffmpeg >/dev/null 2>&1 || return 1
  command -v ffprobe >/dev/null 2>&1 || return 1
  ffmpeg -version >/dev/null 2>&1 || return 1
  ffprobe -version >/dev/null 2>&1 || return 1
  return 0
}

if [ "$(uname -s)" != "Darwin" ]; then
  say_error "VideoMaker использует MLX Whisper и рассчитан на macOS с Apple Silicon. Текущая система: $(uname -s)."
  exit 1
fi
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  say_error "Требуется нативный ARM64 shell на Apple Silicon. Сейчас архитектура shell: $ARCH. Не запускайте Terminal через Rosetta."
  exit 1
fi
MAC_VERSION="$(sw_vers -productVersion 2>/dev/null || echo 0.0)"
MAC_MAJOR="${MAC_VERSION%%.*}"
if ! [[ "$MAC_MAJOR" =~ ^[0-9]+$ ]] || [ "$MAC_MAJOR" -lt 14 ]; then
  say_error "MLX Whisper требует macOS 14 или новее. Обнаружена macOS $MAC_VERSION."
  exit 1
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  if ! system_media_tools_ok; then
    say_error "Не найдены работающие системные ffmpeg и ffprobe. Запустите ./start.command для установки через Homebrew."
    exit 1
  fi
  if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
    say_error "Не найдена готовая .venv. Запустите ./start.command — окружение будет установлено автоматически."
    exit 1
  fi
  EXPECTED="$(requirements_hash)"
  STORED="$(cat "$PROJECT_DIR/.venv/.requirements-runtime-sha256" 2>/dev/null || true)"
  if [ "$EXPECTED" != "$STORED" ]; then
    say_error "Runtime-зависимости изменились или marker отсутствует. Запустите ./start.command для обновления окружения."
    exit 1
  fi
  runtime_import_check "$PROJECT_DIR/.venv/bin/python" || exit 1
  exit 0
fi

# Some audio/checkpoint code paths use system ffmpeg/ffprobe from PATH.
# Install them automatically through an existing Homebrew installation when missing.
if ! system_media_tools_ok; then
  if ! command -v brew >/dev/null 2>&1; then
    say_error "Для некоторых аудио/проверочных операций нужны ffmpeg и ffprobe. Homebrew не найден; установите Homebrew с https://brew.sh/, затем снова запустите start.command."
    exit 1
  fi
  say_info "Системные ffmpeg/ffprobe отсутствуют или не запускаются — устанавливаю FFmpeg через Homebrew."
  if brew list --versions ffmpeg >/dev/null 2>&1; then
    brew reinstall ffmpeg || { say_error "Homebrew не смог переустановить FFmpeg"; exit 1; }
  else
    brew install ffmpeg || { say_error "Homebrew не смог установить FFmpeg"; exit 1; }
  fi
  if ! system_media_tools_ok; then
    say_error "После установки Homebrew ffmpeg/ffprobe не прошли проверку. Проверьте PATH и вывод выше."
    exit 1
  fi
fi

# Prefer broadly supported native Python versions, while allowing newer installed versions.
CANDIDATES=(
  /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.14
  /opt/homebrew/bin/python3.11 /usr/local/bin/python3.13 /usr/local/bin/python3.12
  /usr/local/bin/python3.14 /usr/local/bin/python3.11
  python3.13 python3.12 python3.14 python3.11 python3
)
VALID_CANDIDATES=()
for candidate in "${CANDIDATES[@]}"; do
  if [[ "$candidate" != */* ]]; then candidate="$(command -v "$candidate" 2>/dev/null || true)"; fi
  [ -n "$candidate" ] && [ -x "$candidate" ] || continue
  duplicate=0
  for found in ${VALID_CANDIDATES[@]+"${VALID_CANDIDATES[@]}"}; do [ "$found" = "$candidate" ] && duplicate=1; done
  [ "$duplicate" -eq 1 ] && continue
  if "$candidate" -c 'import platform,sys; sys.exit(0 if sys.version_info >= (3,11) and sys.version_info < (3,15) and platform.machine().lower() in ("arm64","aarch64") else 1)' >/dev/null 2>&1; then
    VALID_CANDIDATES+=("$candidate")
  fi
done
if [ "${#VALID_CANDIDATES[@]}" -eq 0 ]; then
  say_error "Не найден совместимый нативный Python 3.11–3.14 для ARM64. Установите Python с https://www.python.org/downloads/macos/ или через Homebrew, затем повторите запуск."
  exit 1
fi

EXPECTED_HASH="$(requirements_hash)"
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  STORED_HASH="$(cat "$PROJECT_DIR/.venv/.requirements-runtime-sha256" 2>/dev/null || true)"
  if [ "$STORED_HASH" = "$EXPECTED_HASH" ] && runtime_import_check "$PROJECT_DIR/.venv/bin/python"; then
    say_info "Окружение уже готово; установка не требуется."
    exit 0
  fi
fi

BACKUP=""
if [ -d "$PROJECT_DIR/.venv" ]; then
  BACKUP="$PROJECT_DIR/.venv.backup.$$.tmp"
  rm -rf "$BACKUP"
  mv "$PROJECT_DIR/.venv" "$BACKUP" || { say_error "Не удалось сохранить прежнюю .venv"; exit 1; }
fi

SUCCESS=0
for PYTHON in ${VALID_CANDIDATES[@]+"${VALID_CANDIDATES[@]}"}; do
  say_info "Пробую Python: $PYTHON ($("$PYTHON" --version 2>&1))"
  rm -rf "$PROJECT_DIR/.venv"
  if ! "$PYTHON" -m venv "$PROJECT_DIR/.venv"; then
    say_error "Не удалось создать venv через $PYTHON; пробую следующий вариант."
    continue
  fi
  VENV_PY="$PROJECT_DIR/.venv/bin/python"
  if ! "$VENV_PY" -m pip install --upgrade pip; then
    say_error "Не удалось обновить pip для $PYTHON; пробую следующий вариант."
    continue
  fi
  if ! "$VENV_PY" -m pip install -r "$PROJECT_DIR/requirements-runtime.txt"; then
    say_error "Runtime-зависимости не установились через $PYTHON; пробую следующий вариант."
    continue
  fi
  if ! "$VENV_PY" -m pip install mlx-whisper; then
    say_error "mlx-whisper не установился через $PYTHON; пробую следующий вариант."
    continue
  fi
  if ! runtime_import_check "$VENV_PY"; then
    say_error "Проверка установленной среды не прошла; пробую следующий вариант."
    continue
  fi
  printf '%s\n' "$EXPECTED_HASH" > "$PROJECT_DIR/.venv/.requirements-runtime-sha256"
  SUCCESS=1
  break
done

if [ "$SUCCESS" -eq 1 ]; then
  [ -n "$BACKUP" ] && rm -rf "$BACKUP"
  say_info "Установка завершена. Следующий запуск откроет приложение."
  say_info "Модель Whisper будет загружена при первом распознавании; нужен доступ к сети и свободное место на диске."
  exit 0
fi
rm -rf "$PROJECT_DIR/.venv"
if [ -n "$BACKUP" ] && [ -d "$BACKUP" ]; then mv "$BACKUP" "$PROJECT_DIR/.venv"; fi
say_error "Не удалось подготовить среду. Прежняя .venv восстановлена, если она была. Проверьте интернет, версию macOS/Python и сообщение об ошибке выше."
exit 1
