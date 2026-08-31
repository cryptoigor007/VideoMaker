#!/bin/bash

PROJECT_DIR="/Users/dreamstore/video_maker"
cd "$PROJECT_DIR" || exit 1

echo "========================================"
echo "       VideoMaker starting..."
echo "========================================"
echo

# Используем Python проекта
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
elif [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/venv/bin/python"
else
    echo "ERROR: Virtual environment not found."
    echo
    echo "Expected:"
    echo "  $PROJECT_DIR/.venv/bin/python"
    echo
    exit 1
fi

echo "Python: $PYTHON"
"$PYTHON" --version
echo

echo "Checking MLX Whisper..."

"$PYTHON" -c "import mlx_whisper; print('MLX Whisper: OK')" 2>&1

if [ $? -ne 0 ]; then
    echo
    echo "ERROR: mlx-whisper is not installed in:"
    echo "$PYTHON"
    echo
    echo "Install with:"
    echo
    echo "\"$PYTHON\" -m pip install mlx-whisper"
    echo
    exit 1
fi

echo
echo "MLX Whisper: OK"
echo
echo "Log file: $PROJECT_DIR/videomeyker.log"
echo

mkdir -p "$PROJECT_DIR"

"$PYTHON" -m video_maker.main 2>&1 | tee -a "$PROJECT_DIR/videomeyker.log"

EXIT_CODE=${PIPESTATUS[0]}

echo
echo "VideoMaker finished with code: $EXIT_CODE"

exit "$EXIT_CODE"