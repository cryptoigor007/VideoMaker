#!/bin/bash
cd "$(dirname "$0")"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "Starting VideoMaker..."
echo "Log file: $HOME/video_maker/videomeyker.log"
mkdir -p "$HOME/video_maker"

python -m video_maker.main 2>&1 | tee -a "$HOME/video_maker/videomeyker.log"
