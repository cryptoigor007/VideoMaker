#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || true
echo "Starting VideoMaker..."
echo "Log file: ~/video_maker/videomeyker.log"
python -m video_maker.main 2>&1 | tee -a "$HOME/video_maker/videomeyker.log"
